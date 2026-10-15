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

When there is no AMI access (no SSH / root on the Issabel box) the second source in
this module, IssabelWebScraper, logs in to the Issabel web panel like a browser and
reads the Operator Panel status stream (or the old "Asterisk Info" page) instead. It is read-only as well and yields the same
PeerState objects, so the database side does not care which source was used.
"""
import json
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

try:  # only needed for the web-panel source; the AMI source works without them
    import requests
    import urllib3
    from bs4 import BeautifulSoup, NavigableString
except ImportError:  # pragma: no cover - reported as a clear error when the scraper is used
    requests = urllib3 = BeautifulSoup = NavigableString = None


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


# ---- Issabel web panel (no AMI needed) ------------------------------------------------------
# Same PeerState objects as the AMI source, read from the Operator Panel of the Issabel
# web UI after a normal form login. Nothing is changed on the PBX.
class PBXWebError(AMIError):
    """Anything that goes wrong while talking to the Issabel web panel (never contains the password)."""


class PBXWebLoginRejected(PBXWebError):
    """The panel refused the user name / password. Retried only after a cool-down so a wrong
    password in the settings cannot lock the account by hammering it every sync cycle."""


DEFAULT_LOGIN_PATH = '/index.php'
# The Operator Panel is the default source: it lists every extension with its registration state and
# IP. The old "Asterisk Info" page (menu=asterisk_info) is still parsed when PBX_WEB_PEERS_PATH points at it.
DEFAULT_PEERS_PATH = '/index.php?menu=control_panel'
PANEL_MODULE = 'control_panel'
PANEL_READ_SECONDS = 20
MAX_STREAM_BYTES = 5_000_000
_PANEL_HASH = re.compile(r'"ESTADO_CLIENTE_HASH"\s*:\s*"([^"]*)"')
LOGIN_RETRY_SECONDS = 300
MAX_PAGE_BYTES = 5_000_000
_LOGIN_FORM = re.compile(r'name\s*=\s*["\']?input_pass', re.IGNORECASE)
_LOGIN_ERROR_MARKERS = ('incorrect username or password', 'invalid username or password', 'login failed')

# `sip show peers` row (chan_sip). Columns: Name/username  Host  Dyn  Forcerport  Comedia  ACL  Port  Status  Description
# The row is read around its two stable anchors: the numeric port followed by the status text.
_SIP_PEER_LINE = re.compile(
    r'^\s*(?P<name>[^\s/]+)(?:/\S*)?\s+(?P<host>\S+)\s+(?:.*?\s)?(?P<port>\d+)\s+'
    r'(?P<status>(?:OK|LAGGED)\s*\(\s*\d+\s*ms\s*\)|UNREACHABLE|UNKNOWN|Unmonitored)(?![A-Za-z])',
    re.IGNORECASE)
# `pjsip show endpoints` row: " Endpoint:  100/100   Not in use    0 of inf" (state only, no address or latency)
_PJSIP_ENDPOINT_LINE = re.compile(
    r'^\s*Endpoint:\s+(?P<name>[^\s/]+)(?:/\S*)?\s+(?P<state>Unavailable|Not in use|In use|Busy|Ringing|Ring\s*Ringing|On\s*Hold|Invalid|Unknown)(?![A-Za-z])',
    re.IGNORECASE)
_PJSIP_OFFLINE = {'unavailable'}
_PJSIP_UNKNOWN = {'invalid', 'unknown'}


def page_to_text(markup):
    """Issabel page (HTML, or plain text from a rawmode page) -> text lines.

    Table rows become one line each (cells joined by spaces) so a table and the raw
    `sip show peers` text inside a <pre> are parsed by the same regular expressions."""
    if BeautifulSoup is None:
        raise PBXWebError('the "beautifulsoup4" package is not installed (pip install -r requirements.txt)')
    soup = BeautifulSoup(markup or '', 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    for row in soup.find_all('tr'):
        cells = [cell.get_text(' ', strip=True) for cell in row.find_all(['td', 'th'], recursive=False)]
        row.replace_with(NavigableString('\n' + ' '.join(cells) + '\n'))
    return soup.get_text('\n')


def _peer_from_sip_line(line):
    match = _SIP_PEER_LINE.match(line)
    if not match:
        return None
    host = match.group('host')
    ip = '' if host in ('(Unspecified)', '-none-', '0.0.0.0') else host
    status_raw = re.sub(r'\s+', ' ', match.group('status')).strip()
    state, rtt = parse_peer_status(status_raw, bool(ip))
    return PeerState(name=match.group('name'), ip=ip, port=int(match.group('port')), status_raw=status_raw, state=state, rtt_ms=rtt)


def _peer_from_pjsip_line(line):
    match = _PJSIP_ENDPOINT_LINE.match(line)
    if not match:
        return None
    status_raw = re.sub(r'\s+', ' ', match.group('state')).strip()
    key = status_raw.lower()
    state = 'offline' if key in _PJSIP_OFFLINE else 'unknown' if key in _PJSIP_UNKNOWN else 'online'
    return PeerState(name=match.group('name'), ip='', port=0, status_raw=status_raw, state=state, rtt_ms=None)


def parse_peer_listing(text):
    """Text of `sip show peers` (or `pjsip show endpoints`) -> {name: PeerState}.
    Header, summary and unrelated lines are ignored; the first row of a name wins."""
    peers = {}
    for line in (text or '').splitlines():
        peer = _peer_from_sip_line(line) or _peer_from_pjsip_line(line)
        if peer and peer.name not in peers:
            peers[peer.name] = peer
    return peers


# ---- Operator Panel (menu=control_panel) status stream ------------------------------------------
# The panel page holds no extension list. The browser opens a stream (action=pbxStatus) whose first
# message is one JSON object: {"pbxchanges": [{"objtype": "phones", "changetype": "create",
# "extension": "100", "channel": "SIP/100", "ip": "192.168.225.66", "registered": true, ...}, ...]}.
# There is no latency in it: online = registered, offline = not registered.
class JsonObjectScanner:
    """Finds the first complete top-level JSON object in a byte stream, whatever surrounds it
    (SSE framing such as "data: ", blank lines, chunk boundaries). Feed it chunks of any size."""

    def __init__(self):
        self.head = b''  # first bytes seen, to recognise a login page instead of data
        self._buf = bytearray()
        self._depth = 0
        self._in_string = False
        self._escape = False

    def feed(self, chunk):
        """Returns the decoded object once it is complete, else None."""
        if len(self.head) < 4096:
            self.head += bytes(chunk[:4096 - len(self.head)])
        for byte in chunk:
            if self._depth == 0 and byte != 0x7B:  # skip everything before the first "{"
                continue
            self._buf.append(byte)
            if self._in_string:
                if self._escape:
                    self._escape = False
                elif byte == 0x5C:
                    self._escape = True
                elif byte == 0x22:
                    self._in_string = False
            elif byte == 0x22:
                self._in_string = True
            elif byte == 0x7B:
                self._depth += 1
            elif byte == 0x7D:
                self._depth -= 1
                if self._depth == 0:
                    try:
                        return json.loads(self._buf.decode('utf-8', 'replace'))
                    except ValueError as exc:
                        raise PBXWebError('the Operator Panel sent a message that is not valid JSON') from exc
        return None


def parse_panel_changes(message):
    """Operator Panel status message -> {extension: PeerState} for the phones in it."""
    peers = {}
    changes = message.get('pbxchanges') if isinstance(message, dict) else None
    for item in changes or []:
        if not isinstance(item, dict) or item.get('objtype') != 'phones' or item.get('changetype') not in ('create', 'update'):
            continue
        name = str(item.get('extension') or '').strip() or str(item.get('channel') or '').rpartition('/')[2].strip()
        if not name:
            continue
        ip = str(item.get('ip') or '').strip()
        if ip in ('-none-', '0.0.0.0', '(Unspecified)'):
            ip = ''
        registered = item.get('registered')
        if registered is True:
            state, raw = 'online', 'Registered'
        elif registered is False:
            state, raw = 'offline', 'Not registered'
        else:
            state, raw = 'unknown', 'Unknown'
        peers[name] = PeerState(name=name, ip=ip, port=0, status_raw=raw, state=state, rtt_ms=None)
    return peers


def _short(exc):
    return f'{exc.__class__.__name__}: {str(exc)[:160]}'


class IssabelWebScraper:
    """Read-only view of the Issabel web panel: form login, session reuse, re-login when the
    session expires, and the extension list from the Operator Panel (or the Asterisk Info page)."""

    def __init__(self, host, username, password, verify_ssl=False, timeout=10.0,
                 login_path=DEFAULT_LOGIN_PATH, peers_path=DEFAULT_PEERS_PATH):
        if requests is None:
            raise PBXWebError('the "requests" package is not installed (pip install -r requirements.txt)')
        host = (host or '').strip().rstrip('/')
        if '://' not in host:
            host = 'https://' + host
        parts = urlsplit(host)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise PBXWebError('PBX_WEB_HOST must look like https://192.168.1.100')
        self.base_url = f'{parts.scheme}://{parts.netloc}'
        self.username, self._password = username, password
        self.verify_ssl = bool(verify_ssl)
        self.timeout = float(timeout)
        self.login_path = login_path if login_path.startswith('/') else '/' + login_path
        self.peers_path = peers_path if peers_path.startswith('/') else '/' + peers_path
        if not self.verify_ssl:  # Issabel ships a self-signed certificate
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = None
        self._logged_in = False
        self._rejected_at = None

    # ---- session ----------------------------------------------------------------------
    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base_url + path, timeout=self.timeout, verify=self.verify_ssl, **kwargs)
        except requests.RequestException as exc:
            raise PBXWebError(f'cannot reach the Issabel web panel {self.base_url} ({_short(exc)})') from exc
        if len(response.content) > MAX_PAGE_BYTES:
            raise PBXWebError('the Issabel page is unexpectedly large; wrong page path?')
        return response

    @staticmethod
    def _is_login_page(text):
        return bool(_LOGIN_FORM.search(text or ''))

    def login(self):
        """Fresh session + form login. Raises PBXWebLoginRejected for a wrong user / password."""
        if self._rejected_at is not None:
            wait = LOGIN_RETRY_SECONDS - (time.monotonic() - self._rejected_at)
            if wait > 0:
                raise PBXWebLoginRejected(f'Issabel rejected the web user name / password; not retrying for {int(wait)} more seconds')
            self._rejected_at = None
        self._logged_in = False
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'Hotel-extensions PBX monitor'
        form = {}
        first = self._request('GET', self.login_path)  # session cookie + any hidden form fields
        if first.status_code < 400 and BeautifulSoup is not None:
            for field in BeautifulSoup(first.text, 'html.parser').find_all('input', attrs={'type': 'hidden'}):
                if field.get('name'):
                    form[field['name']] = field.get('value', '')
        form.update({'input_user': self.username, 'input_pass': self._password, 'submit_login': 'Submit'})
        response = self._request('POST', self.login_path, data=form)
        if response.status_code >= 400:
            raise PBXWebError(f'Issabel web login failed: HTTP {response.status_code}')
        if self._is_login_page(response.text):
            self._rejected_at = time.monotonic()
            lowered = response.text.lower()
            if any(marker in lowered for marker in _LOGIN_ERROR_MARKERS):
                raise PBXWebLoginRejected('Issabel rejected the web user name / password')
            raise PBXWebLoginRejected('Issabel web login failed: the login form is still shown after sign-in')
        if not self.session.cookies:
            raise PBXWebError('Issabel web login gave no session cookie')
        self._logged_in = True

    def fetch_page(self):
        """HTML of the peers page. Logs in on first use and once more if the session has expired."""
        for attempt in (1, 2):
            if not self._logged_in:
                self.login()
            response = self._request('GET', self.peers_path)
            if response.status_code in (401, 403) or (response.status_code < 400 and self._is_login_page(response.text)):
                self._logged_in = False  # session expired (or was invalidated): sign in again, once
                if attempt == 1:
                    continue
                raise PBXWebError('Issabel kept asking for a login right after signing in')
            if response.status_code >= 400:
                raise PBXWebError(f'Issabel page {self.peers_path} answered HTTP {response.status_code}')
            return response.text
        raise PBXWebError('could not read the Issabel peers page')  # pragma: no cover

    # ---- data -------------------------------------------------------------------------
    def _panel_shutdown(self):
        """The panel's own \"window closed\" call, so no status listener stays open on the PBX."""
        try:
            self._request('POST', f'/index.php?menu={PANEL_MODULE}&rawmode=yes',
                          data={'menu': PANEL_MODULE, 'rawmode': 'yes', 'action': 'pbxStatusShutdown'})
        except PBXWebError:
            pass

    def _read_panel_message(self, state_hash):
        """First status message of the Operator Panel stream, or None when the session is gone."""
        params = {'menu': PANEL_MODULE, 'rawmode': 'yes', 'clientstatehash': state_hash,
                  'action': 'pbxStatus', 'serverevents': 'true'}
        scanner, message, response = JsonObjectScanner(), None, None
        deadline, received = time.monotonic() + PANEL_READ_SECONDS, 0
        try:
            response = self.session.get(self.base_url + '/index.php', params=params, stream=True, timeout=(5, 8),
                                        verify=self.verify_ssl, headers={'Accept': 'text/event-stream'})
            if response.status_code in (401, 403):
                return None
            if response.status_code >= 400:
                raise PBXWebError(f'Issabel Operator Panel stream answered HTTP {response.status_code}')
            for chunk in response.iter_content(chunk_size=1):
                received += len(chunk)
                message = scanner.feed(chunk)
                if message is not None:
                    break
                if received > MAX_STREAM_BYTES or time.monotonic() > deadline:
                    raise PBXWebError('the Operator Panel status stream did not finish in time')
        except requests.RequestException as exc:
            if message is None:
                raise PBXWebError(f'cannot read the Operator Panel status stream ({_short(exc)})') from exc
        finally:
            if response is not None:
                response.close()
            self._panel_shutdown()
        if message is None:
            if self._is_login_page(scanner.head.decode('utf-8', 'replace')):
                return None
            raise PBXWebError('the Operator Panel status stream ended without data')
        if isinstance(message, dict) and message.get('statusResponse') == 'ERROR_SESSION':
            return None
        return message

    def fetch_panel_states(self):
        """{name: PeerState} from the Operator Panel. Logs in again once if the session expired."""
        for attempt in (1, 2):
            page = self.fetch_page()  # also gives the client state hash the stream needs
            found = _PANEL_HASH.search(page)
            message = self._read_panel_message(found.group(1) if found else '')
            if message is None:
                self._logged_in = False
                if attempt == 1:
                    continue
                raise PBXWebError('Issabel kept asking for a login while reading the Operator Panel')
            if message.get('error'):
                raise PBXWebError(f'the Operator Panel reported an error: {str(message["error"])[:160]}')
            return parse_panel_changes(message)
        raise PBXWebError('could not read the Operator Panel')  # pragma: no cover

    def fetch_peer_states(self):
        """{name: PeerState} for every peer. An empty result is an error (wrong path, no permission
        for the module, unknown layout) and never means \"everything is gone\"."""
        if PANEL_MODULE in self.peers_path:
            peers = self.fetch_panel_states()
            if not peers:
                raise PBXWebError('the Operator Panel listed no extensions: check that this user may open \"Operator Panel\"')
            return peers
        peers = parse_peer_listing(page_to_text(self.fetch_page()))
        if not peers:
            raise PBXWebError('no SIP peers found on the Issabel page: check PBX_WEB_PEERS_PATH and that this user may open \"Asterisk Info\"')
        return peers

    def get_peer_statuses(self):
        """{ "101": {"status": "online", "latency": "15ms", "raw_status": "OK (15 ms)", "ip": "..."} }"""
        return {
            name: {'status': peer.state, 'latency': f'{peer.rtt_ms}ms' if peer.rtt_ms is not None else None,
                   'raw_status': peer.status_raw, 'ip': peer.ip}
            for name, peer in self.fetch_peer_states().items()
        }


_scraper_cache = {'key': None, 'scraper': None}


def shared_scraper(host, username, password, verify_ssl=False, timeout=10.0, login_path=DEFAULT_LOGIN_PATH, peers_path=DEFAULT_PEERS_PATH):
    """One scraper per configuration, so the login session is reused between sync cycles."""
    key = (host, username, password, bool(verify_ssl), float(timeout), login_path, peers_path)
    if _scraper_cache['key'] != key:
        _scraper_cache['scraper'] = IssabelWebScraper(host, username, password, verify_ssl, timeout, login_path, peers_path)
        _scraper_cache['key'] = key
    return _scraper_cache['scraper']