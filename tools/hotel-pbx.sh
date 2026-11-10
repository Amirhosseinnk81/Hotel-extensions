#!/bin/bash
# hotel-pbx - the ONLY thing the Araz Desk SSH key may run on the Issabel server.
#
# Install as /usr/local/sbin/hotel-pbx (root:root, mode 755) and call it from authorized_keys as a
# forced command (see DEPLOYMENT.md section 14):
#     command="sudo -u asterisk /usr/local/sbin/hotel-pbx ro",from="<APP_IP>",no-pty,... ssh-ed25519 AAAA...
#     command="sudo -u asterisk /usr/local/sbin/hotel-pbx rw",from="<APP_IP>",no-pty,... ssh-ed25519 AAAA...
#
# The request arrives in SSH_ORIGINAL_COMMAND and must be one of the fixed words below with arguments
# that match a strict pattern; nothing from the request is ever passed to a shell or used as a command.
#   ro mode (read-only):
#     ping                       -> "OK hotel-pbx <mode>"
#     astdb FAMILY [FAMILY ...]  -> AstDB dump of DND CF CFB CFU DAYNIGHT TC HOTELTEST
#     core                       -> uptime in seconds, calls in progress
#     driver                     -> driver=sip | driver=pjsip
#     peers                      -> sip show peers / pjsip show endpoints
#     registry                   -> sip show registry / pjsip show registrations
#     system                     -> CPU, memory, swap, disks, service states
#   rw mode (the day/night key) adds:
#     daynight-set N DAY|NIGHT 0|1   -> database put DAYNIGHT C<N>, and the BLF device state when 1
#     probe-set VALUE                -> database put HOTELTEST probe VALUE (harmless write test)

set -u
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C

MODE="${1:-ro}"
REQUEST="${SSH_ORIGINAL_COMMAND:-}"
ASTERISK="${HOTEL_PBX_ASTERISK:-asterisk}"   # tests point this at a fake

fail() { echo "ERR $1"; exit 2; }
ast() { "$ASTERISK" -rx "$1" 2>&1; }

case "$MODE" in ro|rw) ;; *) fail "bad mode" ;; esac
# letters, digits, space, dash, underscore only: no quotes, no shell characters, no paths
[[ "$REQUEST" =~ ^[A-Za-z0-9_\ -]{1,200}$ ]] || fail "request not allowed"
read -r -a WORDS <<< "$REQUEST"
COMMAND="${WORDS[0]:-}"
ARGS=("${WORDS[@]:1}")

loaded() { ast "module show like $1" | grep -q "^1 modules loaded"; }
driver() { if loaded chan_sip.so; then echo sip; elif loaded res_pjsip.so; then echo pjsip; else echo none; fi; }

case "$COMMAND" in
  ping)
    echo "OK hotel-pbx $MODE"
    ;;
  astdb)
    [ "${#ARGS[@]}" -ge 1 ] && [ "${#ARGS[@]}" -le 8 ] || fail "astdb needs 1 to 8 families"
    for family in "${ARGS[@]}"; do
      case "$family" in DND|CF|CFB|CFU|DAYNIGHT|TC|HOTELTEST) ;; *) fail "family not allowed" ;; esac
    done
    for family in "${ARGS[@]}"; do
      echo "== $family =="
      ast "database show $family"
    done
    ;;
  core)
    [ "${#ARGS[@]}" -eq 0 ] || fail "core takes no arguments"
    ast "core show uptime seconds"
    ast "core show channels count"
    ;;
  driver)
    echo "driver=$(driver)"
    ;;
  peers)
    echo "driver=$(driver)"
    if [ "$(driver)" = pjsip ]; then ast "pjsip show endpoints"; else ast "sip show peers"; fi
    ;;
  registry)
    echo "driver=$(driver)"
    if [ "$(driver)" = pjsip ]; then ast "pjsip show registrations"; else ast "sip show registry"; fi
    ;;
  system)
    [ "${#ARGS[@]}" -eq 0 ] || fail "system takes no arguments"
    read -r _ a1 b1 c1 d1 e1 f1 g1 h1 _ < /proc/stat
    sleep 1
    read -r _ a2 b2 c2 d2 e2 f2 g2 h2 _ < /proc/stat
    busy=$(( (a2 + b2 + c2 + f2 + g2 + h2) - (a1 + b1 + c1 + f1 + g1 + h1) ))
    total=$(( busy + (d2 + e2) - (d1 + e1) ))
    [ "$total" -gt 0 ] && echo "cpu=$(( 100 * busy / total ))"
    awk '/^(MemTotal|MemAvailable|MemFree|Buffers|Cached|SwapTotal|SwapFree):/ { sub(":", "", $1); print "mem_" $1 "=" $2 }' /proc/meminfo
    df -P -x tmpfs -x devtmpfs 2>/dev/null | awk 'NR > 1 { sub("%", "", $5); print "disk=" $6 " " $5 }'
    for service in asterisk mariadb mysqld httpd; do
      state=$(systemctl is-active "$service" 2>/dev/null)
      [ "$state" = unknown ] || [ -z "$state" ] || echo "service=$service $state"
    done
    ;;
  daynight-set)
    [ "$MODE" = rw ] || fail "not allowed with this key"
    [ "${#ARGS[@]}" -eq 3 ] || fail "usage: daynight-set N DAY|NIGHT 0|1"
    [[ "${ARGS[0]}" =~ ^[0-9]$ ]] || fail "bad index"
    case "${ARGS[1]}" in DAY) lamp=NOT_INUSE ;; NIGHT) lamp=INUSE ;; *) fail "bad state" ;; esac
    [[ "${ARGS[2]}" =~ ^[01]$ ]] || fail "bad blf flag"
    out=$(ast "database put DAYNIGHT C${ARGS[0]} ${ARGS[1]}")
    echo "$out" | grep -qi "updated database successfully" || fail "state not written: $out"
    echo "OK state"
    if [ "${ARGS[2]}" = 1 ]; then
      out=$(ast "devstate change Custom:DAYNIGHT${ARGS[0]} $lamp")
      if echo "$out" | grep -qiE "changing|changed"; then echo "OK blf"; else echo "WARN blf: $out"; fi
    fi
    ;;
  probe-set)
    [ "$MODE" = rw ] || fail "not allowed with this key"
    [ "${#ARGS[@]}" -eq 1 ] && [[ "${ARGS[0]}" =~ ^[A-Za-z0-9]{1,20}$ ]] || fail "bad probe value"
    out=$(ast "database put HOTELTEST probe ${ARGS[0]}")
    echo "$out" | grep -qi "updated database successfully" || fail "probe not written: $out"
    echo "OK probe"
    ;;
  *)
    fail "command not allowed"
    ;;
esac
exit 0
