#!/usr/bin/env bash
# collect_pbx_info.sh - READ-ONLY inventory of an Issabel / Asterisk PBX.
#
# Used to build the Hotel-extensions PBX sync: it finds out which SIP channel
# driver is in use (chan_sip or PJSIP), what the peer/contact tables look like,
# and what the phones (Yealink / Htek) put in their SIP User-Agent (some
# firmware includes the MAC address there).
#
# It only runs "show" style commands (asterisk -rx ..., rpm, ss, ip, ls).
# It changes nothing and never reads configuration files that hold passwords.
#
# Usage (as root, or any user allowed to run "asterisk -rx"):
#   bash collect_pbx_info.sh
# Options (environment variables):
#   MASK=0               keep real IPs / MACs (default 1: last octet of private IPv4 addresses -> x,
#                        last 3 MAC bytes -> xx; firmware versions are left intact)
#   FULL=1               include the complete peer / endpoint tables (default: first lines only)
#   MAX_PEERS=60         how many registered peers to look at when sampling User-Agents
#   SAMPLE_PER_VENDOR=2  detailed dumps per phone vendor
#   OUT=/path/report.txt output file (default /tmp/pbx_report_<date>.txt)

set -u
MASK="${MASK:-1}"
FULL="${FULL:-0}"
MAX_PEERS="${MAX_PEERS:-60}"
SAMPLE_PER_VENDOR="${SAMPLE_PER_VENDOR:-2}"
OUT="${OUT:-/tmp/pbx_report_$(date +%Y%m%d_%H%M%S).txt}"
RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT

ast() { asterisk -rx "$1" 2>&1; }
section() { printf '\n===== %s =====\n' "$1" >>"$RAW"; }
put() { cat >>"$RAW"; }
# Never let anything that looks like a secret into the report.
scrub() { grep -v -i -E 'secret *:|md5secret|password|md5_cred|auth_password'; }
head_or_full() { if [ "$FULL" = "1" ]; then cat; else head -n "${1:-30}"; fi; }

mask() {
  if [ "$MASK" = "1" ]; then
    sed -E \
      -e 's/\b(10\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3})\.[0-9]{1,3}\b/\1.x/g' \
      -e 's/(([0-9A-Fa-f]{2}[:-]){3})[0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}/\1xx:xx:xx/g' \
      -e 's/\b([0-9A-Fa-f]{6})[0-9A-Fa-f]{6}\b/\1xxxxxx/g'
  else
    cat
  fi
}

if ! command -v asterisk >/dev/null 2>&1; then
  echo "asterisk command not found. Run this on the Issabel server (as root)." >&2
  exit 1
fi

{
  echo "Report generated: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "Masking: $([ "$MASK" = "1" ] && echo "on (IPv4 last octet, MAC last 3 bytes)" || echo "off")"
} | put

section "System"
{
  cat /etc/redhat-release 2>/dev/null
  uname -r
  asterisk -V 2>&1
  rpm -qa 2>/dev/null | grep -i -E 'issabel|asterisk|endpoint' | sort | head -30
} | put

section "Channel drivers loaded"
{
  ast "module show like chan_sip"
  ast "module show like res_pjsip.so"
} | put

section "AMI (manager) settings - no secrets are printed"
{
  ast "manager show settings"
  echo "--- listening on 5038? ---"
  (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -E ':5038\b' || echo "nothing listening on 5038"
} | scrub | put

section "Server addresses"
{
  ip -4 addr show 2>/dev/null | grep -E 'inet ' 
  ip route 2>/dev/null | head -5
} | put

section "Provisioning files (Issabel Endpoint Configurator / tftp), file names only"
{
  for d in /tftpboot /var/lib/tftpboot; do
    if [ -d "$d" ]; then echo "$d: $(ls "$d" 2>/dev/null | wc -l) files"; ls "$d" 2>/dev/null | head -15; fi
  done
} | put

# ---------------------------------------------------------------- chan_sip
SIP_PEERS="$(ast 'sip show peers' 2>&1)"
if echo "$SIP_PEERS" | grep -q -i -E '^Name/username'; then
  section "chan_sip: sip show peers ($([ "$FULL" = "1" ] && echo full || echo first 40 lines))"
  echo "$SIP_PEERS" | head_or_full 40 | put
  echo "..." | put
  echo "$SIP_PEERS" | tail -n 3 | put

  section "chan_sip: status summary (counts of the Status column)"
  echo "$SIP_PEERS" | awk '
    /^Name\/username/ {next}
    /sip peers \[/ {next}
    / OK \(/ {ok++; next}
    /UNREACHABLE/ {unr++; next}
    /LAGGED/ {lag++; next}
    /Unmonitored/ {unm++; next}
    /UNKNOWN/ {unk++; next}
    NF>0 {other++}
    END {printf "OK=%d LAGGED=%d UNREACHABLE=%d UNKNOWN=%d Unmonitored=%d other=%d\n", ok, lag, unr, unk, unm, other}' | put

  section "chan_sip: User-Agent per vendor (first $MAX_PEERS registered peers)"
  declare -A VENDOR_COUNT=()
  declare -A VENDOR_SAMPLES=()
  declare -A VENDOR_EXAMPLE=()
  scanned=0
  for peer in $(echo "$SIP_PEERS" | awk '/ OK \(/ {split($1, a, "/"); print a[1]}' | head -n "$MAX_PEERS"); do
    detail="$(ast "sip show peer $peer" | scrub)"
    ua="$(echo "$detail" | grep -i -m1 -E '^ *Useragent' | sed -E 's/^[^:]*: *//')"
    [ -z "$ua" ] && ua="(no useragent)"
    vendor="$(echo "$ua" | awk '{print tolower($1)}')"
    VENDOR_COUNT[$vendor]=$(( ${VENDOR_COUNT[$vendor]:-0} + 1 ))
    VENDOR_EXAMPLE[$vendor]="$ua"
    if [ "${VENDOR_SAMPLES[$vendor]:-0}" -lt "$SAMPLE_PER_VENDOR" ]; then
      VENDOR_SAMPLES[$vendor]=$(( ${VENDOR_SAMPLES[$vendor]:-0} + 1 ))
      {
        echo "--- peer $peer ---"
        echo "$detail" | grep -i -E '^ *(\* Name|Name|Status|Addr->IP|Useragent|Reg\. Contact|Qualify|Transport|Callerid|Dynamic|Context|Last-Message) '
      } | put
    fi
    scanned=$((scanned + 1))
  done
  {
    echo "--- vendors seen among $scanned scanned peers ---"
    for v in "${!VENDOR_COUNT[@]}"; do printf '%-20s %3d   e.g. User-Agent: %s\n' "$v" "${VENDOR_COUNT[$v]}" "${VENDOR_EXAMPLE[$v]}"; done
  } | put
else
  section "chan_sip: not in use (no 'Name/username' table)"
  echo "$SIP_PEERS" | head -n 5 | put
fi

# ---------------------------------------------------------------- PJSIP
PJ_ENDPOINTS="$(ast 'pjsip show endpoints' 2>&1)"
if echo "$PJ_ENDPOINTS" | grep -q -E '^ *Endpoint: '; then
  section "PJSIP: pjsip show endpoints ($([ "$FULL" = "1" ] && echo full || echo first 40 lines))"
  echo "$PJ_ENDPOINTS" | head_or_full 40 | put
  section "PJSIP: pjsip show contacts ($([ "$FULL" = "1" ] && echo full || echo first 30 lines))"
  ast "pjsip show contacts" | head_or_full 30 | put
  section "PJSIP: sample endpoints (secrets scrubbed)"
  for ep in $(echo "$PJ_ENDPOINTS" | awk '/^ *Endpoint: +[0-9]/ {split($2, a, "/"); print a[1]}' | head -n 2); do
    { echo "--- endpoint $ep ---"; ast "pjsip show endpoint $ep" | scrub | head -n 60; } | put
  done
else
  section "PJSIP: no endpoints found"
  echo "$PJ_ENDPOINTS" | head -n 3 | put
fi

mask <"$RAW" >"$OUT"
echo "Report written to: $OUT"
echo "Lines: $(wc -l <"$OUT")   (IPs/MACs masked: $([ "$MASK" = "1" ] && echo yes || echo no))"
echo "Send the content of that file (e.g. 'cat $OUT') - it contains no passwords."
