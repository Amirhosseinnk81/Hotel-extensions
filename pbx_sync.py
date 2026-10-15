"""Read-only link to the hotel PBX (Issabel / Asterisk with chan_sip) over AMI.

This module only speaks the Asterisk Manager Interface protocol and parses the
answers; it knows nothing about the database or Flask. The database side lives
in app.py (sync_pbx_once).

Only two AMI actions are used, both read-only:
  * SIPpeers     - every SIP peer with its address and qualify status
  * SIPshowpeer  - one peer in detail (we only use its SIP-Useragent)
Both need "write = reporting" (or "system") on the AMI user and no "read"
class at all, because the client logs in with "Events: off".

Field names and status strings were checked against the Asterisk 18.6.0 source
(channels/chan_sip.c): PeerEntry events, the SIPshowpeer response and the
status text produced by peer_status().
"""
import re
import socket
import time
from dataclasses import dataclass


class AMIError(Exception):
    """Anything that goes wrong while talking to the PBX (never contains the secret)."""


class AMIClient:
    """Tiny blocking AMI client. Use it as a context manager; one short-lived
    connection per sync cycle keeps things simple and robust."""

    def __init__(self, host, port=5038, username='', secret='', timeout=5.0, max_seconds=30.0):
        self.host, self.port = host, int(port)
        self.username, self.secret = username, secret
        self.timeout = float(timeout)
        self.max_seconds = float(max_seconds)
        self._sock = None
        self._buffer = b''
        self._deadline = 0.0

    # ---- connection ------------------------------------------------------------
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def connect(self):
        self._deadline = time.monotonic() + self.max_seconds
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise AMIError(f'cannot connect to {self.host}:{self.port} ({exc.__class__.__name__}: {exc})') from exc
        self._sock.settimeout(self.timeout)
        banner = self._read_until(b'\r\n').decode('utf-8', 'replace').strip()
        if not banner.lower().startswith('asterisk call manager'):
            raise AMIError(f'unexpected banner from {self.host}:{self.port}: {banner[:60]!r}')
        reply = self.action('Login', Username=self.username, Secret=self.secret, Events='off')
        if reply.get('Response') != 'Success':
            raise AMIError('AMI login failed: ' + reply.get('Message', 'no message'))

    def close(self):
        if self._sock is None:
            return
        try:
            self._sock.sendall(b'Action: Logoff\r\n\r\n')
        except OSError:
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None

    # ---- low level I/O -----------------------------------------------------------
    def _recv(self):
        if time.monotonic() > self._deadline:
            raise AMIError('AMI exchange took too long')
        try:
            chunk = self._sock.recv(65536)
        except socket.timeout as exc:
            raise AMIError('timed out waiting for the PBX') from exc
        except OSError as exc:
            raise AMIError(f'connection error: {exc}') from exc
        if not chunk:
            raise AMIError('PBX closed the connection')
        return chunk

    def _read_until(self, marker):
        while marker not in self._buffer:
            self._buffer += self._recv()
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _read_message(self):
        """Next AMI message (a block of 'Key: value' lines) as a dict."""
        raw = self._read_until(b'\r\n\r\n').decode('utf-8', 'replace')
        message = {}
        for line in raw.split('\r\n'):
            if ':' in line:
                key, _, value = line.partition(':')
                message[key.strip()] = value.strip()
        return message

    def _send(self, action, fields):
        for value in fields.values():
            if '\r' in str(value) or '\n' in str(value):
                raise AMIError('refusing to send a field containing a line break')
        lines = [f'Action: {action}'] + [f'{key}: {value}' for key, value in fields.items()]
        try:
            self._sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode('utf-8'))
        except OSError as exc:
            raise AMIError(f'connection error: {exc}') from exc

    # ---- actions -----------------------------------------------------------------
    def action(self, name, **fields):
        """Run an action that answers with a single Response message."""
        self._send(name, fields)
        while True:
            message = self._read_message()
            if 'Response' in message:
                return message

    def list_action(self, name, **fields):
        """Run an action that answers with an event list (e.g. SIPpeers) -> list of event dicts."""
        self._send(name, fields)
        first = self._read_message()
        while 'Response' not in first:
            first = self._read_message()
        if first.get('Response') != 'Success':
            raise AMIError(f'{name} failed: ' + first.get('Message', 'no message'))
        events = []
        while True:
            message = self._read_message()
            if message.get('EventList') == 'Complete':
                return events
            if 'Event' in message:
                events.append(message)


# ---- parsing ------------------------------------------------------------------------
_STATUS_OK = re.compile(r'^(OK|LAGGED)\s*\(\s*(\d+)\s*ms\s*\)', re.IGNORECASE)


@dataclass
class PeerState:
    name: str
    ip: str  # '' when the peer has no address (not registered)
    port: int
    status_raw: str
    state: str  # online / offline / unknown
    rtt_ms: object  # int or None


def parse_peer_status(status, has_ip):
    """Map chan_sip's qualify status text to ('online' | 'offline' | 'unknown', rtt_ms).

    OK / LAGGED (n ms)  -> online (LAGGED is slow but still answering)
    UNREACHABLE         -> offline
    UNKNOWN             -> offline without an address (not registered), otherwise unknown
                           (just registered, qualify not answered yet)
    Unmonitored         -> qualify is off: online when it has an address, else offline
    """
    text = (status or '').strip()
    match = _STATUS_OK.match(text)
    if match:
        return 'online', int(match.group(2))
    upper = text.upper()
    if upper.startswith('UNREACHABLE'):
        return 'offline', None
    if upper.startswith('UNKNOWN'):
        return ('unknown', None) if has_ip else ('offline', None)
    if upper.startswith('UNMONITORED'):
        return ('online', None) if has_ip else ('offline', None)
    return 'unknown', None


def peer_from_event(event):
    """PeerEntry event -> PeerState, or None for entries that are not peers."""
    if event.get('Event') != 'PeerEntry' or event.get('ChanObjectType', 'peer') != 'peer':
        return None
    name = event.get('ObjectName', '').strip()
    if not name:
        return None
    ip = event.get('IPaddress', '').strip()
    if ip in ('-none-', '0.0.0.0', '(Unspecified)'):
        ip = ''
    try:
        port = int(event.get('IPport', '0') or 0)
    except ValueError:
        port = 0
    status_raw = event.get('Status', '')
    state, rtt = parse_peer_status(status_raw, bool(ip))
    return PeerState(name=name, ip=ip, port=port, status_raw=status_raw, state=state, rtt_ms=rtt)


def fetch_peers(client):
    """All SIP peers known to the PBX -> {name: PeerState}."""
    peers = {}
    for event in client.list_action('SIPpeers'):
        peer = peer_from_event(event)
        if peer:
            peers[peer.name] = peer
    return peers


def fetch_user_agent(client, name):
    """SIP-Useragent of one peer, or '' when the PBX has none / does not know the peer."""
    reply = client.action('SIPshowpeer', Peer=name)
    if reply.get('Response') != 'Success':
        return ''
    return reply.get('SIP-Useragent', '').strip()


_VERSION = re.compile(r'^\d+(?:\.\d+){2,}$')
_MAC_TOKEN = re.compile(r'^(?:[0-9A-Fa-f]{2}[:\-]?){5}[0-9A-Fa-f]{2}$')


def parse_user_agent(user_agent):
    """Split a phone's SIP User-Agent into vendor / model / firmware / MAC.

    Real examples from the hotel PBX:
      'Yealink SIP-T31P 124.86.0.40'   -> Yealink, SIP-T31P, firmware 124.86.0.40 (no MAC)
      'Htek UC902 001fc11f5ddd'        -> Htek, UC902, MAC 00:1F:C1:1F:5D:DD
    """
    result = {'vendor': None, 'model': None, 'firmware': None, 'mac': None, 'display_model': None}
    tokens = (user_agent or '').split()
    if not tokens:
        return result
    result['vendor'] = tokens[0]
    for token in tokens[1:]:
        if _VERSION.match(token):
            result['firmware'] = result['firmware'] or token
        elif _MAC_TOKEN.match(token) and result['mac'] is None:
            digits = re.sub(r'[^0-9A-Fa-f]', '', token).upper()
            if digits not in ('000000000000', 'FFFFFFFFFFFF'):
                result['mac'] = ':'.join(digits[i:i + 2] for i in range(0, 12, 2))
        elif result['model'] is None:
            result['model'] = token
    if result['model']:
        model = result['model']
        model = model[4:] if model.upper().startswith('SIP-') else model
        result['display_model'] = f"{result['vendor']} {model}"
    return result
