"""The one place the newer PBX features (call report, DND / forward, health, day / night) talk to Issabel.

Pure connection code: no Flask, no database. Every feature goes through these classes instead of opening
its own sockets, so timeouts, error wording and secret handling live in one file.

  AmiReader  - read-only AMI session (DBGet, CoreStatus, trunk registry, SIP driver detection).
               One short-lived connection per snapshot, opened with `with AmiReader(settings) as ami:`.
  SshReader  - the same reads over SSH, as an alternative to AMI: a restricted key whose only possible
               command is tools/hotel-pbx.sh on the PBX (forced command). Also CPU / memory / disk.
  open_reader / open_writer - pick AMI or SSH (PBX_LINK); features call these, never a class directly.
  CdrClient  - read-only MySQL access to asteriskcdrdb.cdr (SELECT only, one connection per query).

Every failure becomes a ConnectorError with a `kind` (config / unreachable / auth / timeout / permission /
driver / protocol / dependency). Its str() is an English line for app.log; `.fa` is the Persian text for
the UI. Neither ever contains a secret.

Settings come only from the environment (.env, see .env.example):
  PBX_AMI_HOST / PBX_AMI_PORT / PBX_AMI_USER / PBX_AMI_SECRET / PBX_AMI_TIMEOUT   - shared with the phone sync
  PBX_AMI_AUTH        md5 (default, the secret never crosses the network) or plain
  PBX_AMI_MAX_SECONDS ceiling for one whole AMI session (default 30)
  PBX_SIP_DRIVER      auto (default) / sip / pjsip
  PBX_CDR_HOST / PBX_CDR_PORT / PBX_CDR_USER / PBX_CDR_PASS / PBX_CDR_DB / PBX_CDR_TIMEOUT
  PBX_SNAPSHOT_TTL    seconds between two reads of the same snapshot (default 120, minimum 30)
  PBX_LINK            auto (default: AMI if configured, else SSH) / ami / ssh
  PBX_SSH_HOST / PBX_SSH_PORT / PBX_SSH_USER / PBX_SSH_KEY / PBX_SSH_WRITE_KEY / PBX_SSH_KNOWN_HOSTS
  PBX_SSH_TIMEOUT / PBX_SSH_MAX_SECONDS / PBX_SSH_BIN
"""
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pbx_daynight
from pbx_sync import AMIClient, AMIError, parse_peer_listing

# ---- errors --------------------------------------------------------------------------------------
FA_MESSAGES = {
    'config': 'این اتصال به ایزابل هنوز در فایل .env تنظیم نشده است.',
    'unreachable': 'سرور ایزابل در دسترس نیست؛ شبکه و فایروال را بررسی کنید.',
    'auth': 'ایزابل نام کاربری یا رمز اتصال را نپذیرفت؛ مقدارهای .env را بررسی کنید.',
    'timeout': 'ایزابل در زمان مقرر پاسخ نداد؛ کمی بعد دوباره امتحان کنید.',
    'permission': 'کاربر اتصال به این بخش از ایزابل دسترسی ندارد.',
    'driver': 'درایور SIP ایزابل تشخیص داده نشد؛ PBX_SIP_DRIVER را در .env تنظیم کنید.',
    'protocol': 'پاسخ ایزابل قابل فهم نبود؛ جزئیات در لاگ سرور ثبت شد.',
    'dependency': 'بسته‌ی لازم روی سرور برنامه نصب نیست (pip install -r requirements.txt).',
    'hostkey': 'کلید شناسایی سرور ایزابل با کلید ثبت‌شده (PBX_SSH_KNOWN_HOSTS) نمی‌خواند؛ تا بررسی نشود اتصال برقرار نمی‌شود.',
}


class ConnectorError(Exception):
    """A PBX call that failed. str() = English detail for the log, .fa = Persian text for the page."""

    def __init__(self, kind, detail, source=''):
        self.kind = kind if kind in FA_MESSAGES else 'protocol'
        self.source = source
        self.detail = str(detail)[:300]
        super().__init__(f'{source or "pbx"} {self.kind}: {self.detail}')

    @property
    def fa(self):
        return FA_MESSAGES[self.kind]


def _ami_error(exc):
    """AMIError text (see pbx_sync.AMIClient) -> ConnectorError of the right kind."""
    text = str(exc)
    lowered = text.lower()
    if lowered.startswith('cannot connect') or 'closed the connection' in lowered or 'unexpected banner' in lowered:
        kind = 'unreachable'
    elif 'login failed' in lowered or 'challenge refused' in lowered:
        kind = 'auth'
    elif 'timed out' in lowered or 'took too long' in lowered:
        kind = 'timeout'
    elif 'permission denied' in lowered:
        kind = 'permission'
    else:
        kind = 'protocol'
    return ConnectorError(kind, text, 'ami')


# ---- settings ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AmiSettings:
    host: str = ''
    port: int = 5038
    user: str = ''
    secret: str = field(default='', repr=False)
    timeout: float = 5.0
    max_seconds: float = 30.0
    auth: str = 'md5'

    @property
    def ready(self):
        return bool(self.host and self.user and self.secret)


@dataclass(frozen=True)
class CdrSettings:
    host: str = ''
    port: int = 3306
    user: str = ''
    password: str = field(default='', repr=False)
    database: str = 'asteriskcdrdb'
    timeout: float = 5.0

    @property
    def ready(self):
        return bool(self.host and self.user and self.password)


@dataclass(frozen=True)
class SshSettings:
    host: str = ''
    port: int = 22
    user: str = ''
    key: str = ''           # private key file of the read-only key (forced command "hotel-pbx ro")
    write_key: str = ''     # private key file of the day / night key ("hotel-pbx rw"); empty = no switching
    known_hosts: str = ''   # file with the PBX's host key: the connection is refused if it does not match
    timeout: float = 10.0   # connect timeout
    max_seconds: float = 30.0
    binary: str = 'ssh'     # Windows 10 / Server 2019+: C:\Windows\System32\OpenSSH\ssh.exe

    @property
    def ready(self):
        return bool(self.host and self.user and self.key and self.known_hosts)


@dataclass(frozen=True)
class Settings:
    ami: AmiSettings
    cdr: CdrSettings
    sip_driver: str = 'auto'
    snapshot_ttl: int = 120
    ami_write: AmiSettings = field(default_factory=AmiSettings)  # separate user, only for switching day / night
    ssh: SshSettings = field(default_factory=SshSettings)
    link_wanted: str = 'auto'

    @property
    def link(self):
        """'ami' / 'ssh' / None: how the features reach AstDB and the Asterisk CLI."""
        if self.link_wanted == 'ami':
            return 'ami' if self.ami.ready else None
        if self.link_wanted == 'ssh':
            return 'ssh' if self.ssh.ready else None
        return 'ami' if self.ami.ready else 'ssh' if self.ssh.ready else None

    @property
    def reader_ready(self):
        return self.link is not None

    @property
    def writer_ready(self):
        return (self.link == 'ami' and self.ami_write.ready) or (self.link == 'ssh' and bool(self.ssh.write_key))


SIP_DRIVERS = ('auto', 'sip', 'pjsip')
LINKS = ('auto', 'ami', 'ssh')
MIN_SNAPSHOT_TTL = 30


def load_settings(environ=None):
    env = os.environ if environ is None else environ

    def text(name, default=''):
        return (env.get(name) or default).strip()

    def number(name, default, cast=int, minimum=None):
        try:
            value = cast(text(name) or default)
        except ValueError:
            value = cast(default)
        return value if minimum is None else max(minimum, value)

    driver = text('PBX_SIP_DRIVER', 'auto').lower()
    link = text('PBX_LINK', 'auto').lower()
    return Settings(
        ami=AmiSettings(host=text('PBX_AMI_HOST'), port=number('PBX_AMI_PORT', '5038'), user=text('PBX_AMI_USER'),
                        secret=env.get('PBX_AMI_SECRET', ''), timeout=number('PBX_AMI_TIMEOUT', '5', float, 1.0),
                        max_seconds=number('PBX_AMI_MAX_SECONDS', '30', float, 5.0),
                        auth='plain' if text('PBX_AMI_AUTH').lower() == 'plain' else 'md5'),
        cdr=CdrSettings(host=text('PBX_CDR_HOST'), port=number('PBX_CDR_PORT', '3306'), user=text('PBX_CDR_USER'),
                        password=env.get('PBX_CDR_PASS', ''), database=text('PBX_CDR_DB', 'asteriskcdrdb'),
                        timeout=number('PBX_CDR_TIMEOUT', '5', float, 1.0)),
        sip_driver=driver if driver in SIP_DRIVERS else 'auto',
        snapshot_ttl=number('PBX_SNAPSHOT_TTL', '120', int, MIN_SNAPSHOT_TTL),
        ami_write=AmiSettings(host=text('PBX_AMI_HOST'), port=number('PBX_AMI_PORT', '5038'), user=text('PBX_AMI_WRITE_USER'),
                              secret=env.get('PBX_AMI_WRITE_SECRET', ''), timeout=number('PBX_AMI_TIMEOUT', '5', float, 1.0),
                              max_seconds=15.0, auth='plain' if text('PBX_AMI_AUTH').lower() == 'plain' else 'md5'),
        ssh=SshSettings(host=text('PBX_SSH_HOST') or text('PBX_AMI_HOST'), port=number('PBX_SSH_PORT', '22'),
                        user=text('PBX_SSH_USER'), key=text('PBX_SSH_KEY'), write_key=text('PBX_SSH_WRITE_KEY'),
                        known_hosts=text('PBX_SSH_KNOWN_HOSTS'), timeout=number('PBX_SSH_TIMEOUT', '10', float, 2.0),
                        max_seconds=number('PBX_SSH_MAX_SECONDS', '30', float, 5.0), binary=text('PBX_SSH_BIN', 'ssh')),
        link_wanted=link if link in LINKS else 'auto',
    )


# ---- AMI (read-only) -----------------------------------------------------------------------------
_DB_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_UNKNOWN_ACTION = 'invalid/unknown command'
_driver_cache = {}  # (host, port) -> 'sip' / 'pjsip', detected once per process


class AmiReader:
    """Read-only AMI session. Only actions that need "write = reporting" (or "system") are used:
    DBGet, CoreStatus, SIPshowregistry / PJSIPShowRegistrationsOutbound and the SIPshowpeer /
    PJSIPShowEndpoint probes of the driver detection. Nothing is ever changed on the PBX."""

    def __init__(self, settings, client_factory=None):
        self.settings = settings
        self.ami = settings.ami if isinstance(settings, Settings) else settings
        self.wanted_driver = settings.sip_driver if isinstance(settings, Settings) else 'auto'
        self._factory = client_factory or (lambda a: AMIClient(a.host, a.port, a.user, a.secret, a.timeout, a.max_seconds, a.auth))
        self._client = None

    def __enter__(self):
        if not self.ami.ready:
            raise ConnectorError('config', 'PBX_AMI_HOST / PBX_AMI_USER / PBX_AMI_SECRET are not all set', 'ami')
        try:
            self._client = self._factory(self.ami)
            self._client.connect()
        except AMIError as exc:
            self._client = None
            raise _ami_error(exc) from exc
        return self

    def __exit__(self, *exc_info):
        if self._client is not None:
            self._client.close()
            self._client = None
        return False

    def _action(self, name, **fields):
        try:
            return self._client.action(name, **fields)
        except AMIError as exc:
            raise _ami_error(exc) from exc

    def _check(self, name, reply):
        if reply.get('Response') == 'Success':
            return reply
        message = reply.get('Message', 'no message')
        kind = 'permission' if 'permission denied' in message.lower() else 'protocol'
        raise ConnectorError(kind, f'{name} failed: {message}', 'ami')

    def _list(self, name, **fields):
        try:
            return self._client.list_action(name, **fields)
        except AMIError as exc:
            raise _ami_error(exc) from exc

    # ---- AstDB ---------------------------------------------------------------------------------
    def db_get(self, family, key):
        """Value of one AstDB key, or None when the key does not exist."""
        if not (_DB_NAME.match(str(family)) and _DB_NAME.match(str(key))):
            raise ValueError(f'invalid AstDB name {family!r}/{key!r}')
        reply = self._action('DBGet', Family=family, Key=key)
        if reply.get('Response') != 'Success':
            if 'not found' in reply.get('Message', '').lower():
                return None
            self._check('DBGet', reply)
        is_list = reply.get('EventList', '').lower() == 'start'  # Asterisk 18 ends the answer with DBGetComplete
        value = None
        try:
            while True:
                message = self._client.read_message()
                if message.get('Event') == 'DBGetResponse':
                    value = message.get('Val', '')
                    if not is_list:
                        return value
                elif message.get('EventList', '').lower() == 'complete' or message.get('Event') == 'DBGetComplete':
                    return value
        except AMIError as exc:
            raise _ami_error(exc) from exc

    def db_get_many(self, family, keys):
        """{key: value} for the keys that exist, all over this one connection."""
        found = {}
        for key in keys:
            value = self.db_get(family, key)
            if value is not None:
                found[str(key)] = value
        return found

    # ---- core ----------------------------------------------------------------------------------
    def core_status(self):
        """{'startup': 'YYYY-MM-DD HH:MM:SS' (PBX local time), 'reload': ..., 'current_calls': int}"""
        reply = self._check('CoreStatus', self._action('CoreStatus'))

        def stamp(prefix):
            parts = [reply.get(f'{prefix}Date', '').strip(), reply.get(f'{prefix}Time', '').strip()]
            return ' '.join(p for p in parts if p) or None

        try:
            calls = int(reply.get('CoreCurrentCalls', '0') or 0)
        except ValueError:
            calls = 0
        return {'startup': stamp('CoreStartup'), 'reload': stamp('CoreReload'), 'current_calls': calls}

    def driver(self):
        """'sip' (chan_sip) or 'pjsip': PBX_SIP_DRIVER, or detected once with harmless probes."""
        if self.wanted_driver in ('sip', 'pjsip'):
            return self.wanted_driver
        key = (self.ami.host, self.ami.port)
        if key not in _driver_cache:
            _driver_cache[key] = self._detect_driver()
        return _driver_cache[key]

    def _detect_driver(self):
        # A missing module answers "Invalid/unknown command"; a loaded one says the probe peer does not exist.
        for driver, action, field_name in (('sip', 'SIPshowpeer', 'Peer'), ('pjsip', 'PJSIPShowEndpoint', 'Endpoint')):
            reply = self._action(action, **{field_name: 'hotelapp-driver-probe'})
            message = reply.get('Message', '').lower()
            if 'permission denied' in message:
                raise ConnectorError('permission', f'{action}: permission denied', 'ami')
            if _UNKNOWN_ACTION not in message:
                return driver
        raise ConnectorError('driver', 'neither chan_sip nor res_pjsip answered', 'ami')

    def peer_names(self):
        """Names of every SIP peer (chan_sip) or endpoint (PJSIP); no display names, AMI does not have them."""
        if self.driver() == 'pjsip':
            events = self._list('PJSIPShowEndpoints')
            return [e.get('ObjectName', '').strip() for e in events if e.get('Event') == 'EndpointList' and e.get('ObjectName')]
        events = self._list('SIPpeers')
        return [e.get('ObjectName', '').strip() for e in events
                if e.get('Event') == 'PeerEntry' and e.get('ChanObjectType', 'peer') == 'peer' and e.get('ObjectName')]

    def registry(self):
        """Outbound trunk registrations as raw AMI events: (driver, [event dict, ...])."""
        driver = self.driver()
        if driver == 'pjsip':
            events = self._list('PJSIPShowRegistrationsOutbound')
            return driver, [e for e in events if e.get('Event') == 'OutboundRegistrationDetail']
        events = self._list('SIPshowregistry')
        return driver, [e for e in events if e.get('Event') == 'RegistryEntry']


class AmiWriter:
    """The only class that changes anything on the PBX. It runs a list of actions prepared and checked
    elsewhere (pbx_daynight.write_actions) once, with the separate write user (PBX_AMI_WRITE_USER).
    No retry: a failed write is reported, never repeated automatically."""

    def __init__(self, settings, client_factory=None):
        self.ami = settings.ami_write
        self._factory = client_factory or (lambda a: AMIClient(a.host, a.port, a.user, a.secret, a.timeout, a.max_seconds, a.auth))

    def run(self, actions):
        """Raises ConnectorError on the first failure; its `completed` attribute says how many actions succeeded."""
        if not self.ami.ready:
            error = ConnectorError('config', 'PBX_AMI_HOST / PBX_AMI_WRITE_USER / PBX_AMI_WRITE_SECRET are not all set', 'ami-write')
            error.completed = 0
            raise error
        completed = 0
        try:
            client = self._factory(self.ami)
            client.connect()
        except AMIError as exc:
            error = _ami_error(exc)
            error.completed = 0
            raise error from exc
        try:
            for name, fields in actions:
                reply = client.action(name, **fields)
                if reply.get('Response') != 'Success':
                    message = reply.get('Message', 'no message')
                    kind = 'permission' if 'permission denied' in message.lower() else 'protocol'
                    error = ConnectorError(kind, f'{name} failed: {message}', 'ami-write')
                    error.completed = completed
                    raise error
                completed += 1
        except AMIError as exc:
            error = _ami_error(exc)
            error.completed = completed
            raise error from exc
        finally:
            client.close()
        return completed

    def set_daynight(self, index, state, method='setvar', devstate=True):
        return self.run(pbx_daynight.write_actions(index, state, method, devstate))

    def set_probe(self, value, method='setvar'):
        return self.run(pbx_daynight.test_actions(method, value))


# ---- SSH (alternative to AMI) --------------------------------------------------------------------
# The key on the PBX can only run tools/hotel-pbx.sh (authorized_keys "command="). What we send is one
# of that script's fixed words with checked arguments; the script checks the same pattern again.
_SSH_REQUEST = re.compile(r'^[A-Za-z0-9_ -]{1,200}$')
SSH_FAMILIES = ('DND', 'CF', 'CFB', 'CFU', 'DAYNIGHT', 'TC', pbx_daynight.TEST_FAMILY)
_ASTDB_LINE = re.compile(r'^/([^/\s]+)/(\S+)\s*:\s?(.*?)\s*$')
_SIP_REGISTRY = re.compile(r'^(?P<host>\S+)\s+[YN]\s+(?P<user>\S+)\s+\d+\s+(?P<state>\S+(?: \S+)*?)(?:\s{2,}.*)?$')
_PJSIP_REGISTRY = re.compile(r'^\s*(?P<name>[^\s/]+)/\S+\s.*?\b(?P<state>Registered|Unregistered|Rejected|Stopped|Failed)\b')


def _ssh_error(returncode, stderr):
    text = ' '.join((stderr or '').split())[:300]
    lowered = text.lower()
    if 'host key verification failed' in lowered or 'remote host identification has changed' in lowered:
        kind = 'hostkey'
    elif 'permission denied' in lowered or 'too many authentication failures' in lowered:
        kind = 'auth'
    elif 'timed out' in lowered:
        kind = 'timeout'
    elif any(word in lowered for word in ('connection refused', 'no route to host', 'could not resolve', 'network is unreachable',
                                          'connection closed', 'connection reset')):
        kind = 'unreachable'
    else:
        kind = 'protocol'
    return ConnectorError(kind, f'ssh exit {returncode}: {text or "no message"}', 'ssh')


class SshRunner:
    """Runs one request through the Windows OpenSSH client. The private key never leaves its file."""

    def __init__(self, ssh):
        self.ssh = ssh

    def command(self, request, write=False):
        key = self.ssh.write_key if write else self.ssh.key
        return [self.ssh.binary, '-i', key, '-p', str(self.ssh.port), '-T',
                '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', f'UserKnownHostsFile={self.ssh.known_hosts}', '-o', f'ConnectTimeout={int(self.ssh.timeout)}',
                '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', '-o', 'LogLevel=ERROR',
                f'{self.ssh.user}@{self.ssh.host}', request]

    def run(self, request, write=False):
        """stdout of the PBX script for one request. Raises ConnectorError (never contains key material)."""
        if not _SSH_REQUEST.match(request or ''):
            raise ValueError(f'refusing to send an odd SSH request {request!r}')
        if not self.ssh.ready or (write and not self.ssh.write_key):
            raise ConnectorError('config', 'PBX_SSH_HOST / PBX_SSH_USER / PBX_SSH_KEY / PBX_SSH_KNOWN_HOSTS'
                                 + (' / PBX_SSH_WRITE_KEY' if write else '') + ' are not all set', 'ssh')
        try:
            done = subprocess.run(self.command(request, write), capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', stdin=subprocess.DEVNULL, timeout=self.ssh.max_seconds)
        except FileNotFoundError as exc:
            raise ConnectorError('dependency', f'the SSH client "{self.ssh.binary}" was not found (Windows: OpenSSH Client feature)', 'ssh') from exc
        except subprocess.TimeoutExpired as exc:
            raise ConnectorError('timeout', f'no answer within {self.ssh.max_seconds:g} s', 'ssh') from exc
        if done.returncode == 255:
            raise _ssh_error(done.returncode, done.stderr)
        out = done.stdout or ''
        if out.startswith('ERR '):
            message = out.splitlines()[0][4:]
            raise ConnectorError('permission' if 'not allowed' in message else 'protocol', f'hotel-pbx: {message}', 'ssh')
        if done.returncode != 0:
            raise _ssh_error(done.returncode, done.stderr or out)
        return out


def parse_astdb(text):
    """Output of 'astdb FAMILY ...' (sections of `database show`) -> {family: {key: value}}."""
    found, current = {}, None
    for line in (text or '').splitlines():
        if line.startswith('== ') and line.endswith(' =='):
            current = line[3:-3].strip()
            found.setdefault(current, {})
            continue
        match = _ASTDB_LINE.match(line)
        if match and current and match.group(1) == current:
            found[current][match.group(2)] = match.group(3)
    return found


def parse_registry_text(driver, text):
    """`sip show registry` / `pjsip show registrations` -> events shaped like AmiReader.registry()."""
    events = []
    for line in (text or '').splitlines():
        if driver == 'pjsip':
            match = _PJSIP_REGISTRY.match(line)
            if match and not line.strip().startswith('<'):
                events.append({'Event': 'OutboundRegistrationDetail', 'ObjectName': match.group('name'), 'Status': match.group('state')})
        else:
            match = _SIP_REGISTRY.match(line.strip())
            if match and match.group('host').lower() != 'host':
                events.append({'Event': 'RegistryEntry', 'Host': match.group('host').rsplit(':', 1)[0],
                               'Username': match.group('user'), 'State': match.group('state').strip()})
    return events


def parse_system(text):
    """Output of 'system' -> the same dict pbx_health.parse_dashboard returns."""
    values, disks, services = {}, [], {}
    for line in (text or '').splitlines():
        key, _, value = line.partition('=')
        if key == 'disk':
            mount, _, percent = value.rpartition(' ')
            if mount.startswith('/') and percent.isdigit():
                disks.append({'mount': mount, 'percent': float(percent)})
        elif key == 'service':
            name, _, state = value.partition(' ')
            name = {'mariadb': 'mysql', 'mysqld': 'mysql', 'httpd': 'apache'}.get(name, name)
            if name in services and services[name] == 'running':
                continue
            services[name] = 'running' if state.strip() == 'active' else 'stopped'
        elif value.strip().isdigit():
            values[key] = int(value.strip())

    def used(total, free):
        return round(100.0 * (values[total] - values[free]) / values[total], 1) if values.get(total) and free in values else None

    memory_free = 'mem_MemAvailable' if 'mem_MemAvailable' in values else 'mem_MemFree'
    return {'cpu': float(values['cpu']) if 'cpu' in values else None, 'memory': used('mem_MemTotal', memory_free),
            'swap': used('mem_SwapTotal', 'mem_SwapFree') if values.get('mem_SwapTotal') else None,
            'disks': disks, 'services': services}


class SshReader:
    """The AmiReader interface (db_get, db_get_many, core_status, driver, registry, peer_names) over SSH,
    plus system() for CPU / memory / disk. One `ssh` run per question; AstDB families are read whole
    and kept for the rest of this `with` block, so a DND snapshot is a single call."""

    def __init__(self, settings, runner=None):
        self.settings, self.ssh = settings, settings.ssh
        self._run = runner or SshRunner(self.ssh).run
        self._astdb = {}
        self._driver = None

    def __enter__(self):
        if not self.ssh.ready:
            raise ConnectorError('config', 'PBX_SSH_HOST / PBX_SSH_USER / PBX_SSH_KEY / PBX_SSH_KNOWN_HOSTS are not all set', 'ssh')
        return self

    def __exit__(self, *exc_info):
        return False

    def prefetch(self, families):
        missing = [f for f in dict.fromkeys(families) if f not in self._astdb]
        for family in missing:
            if family not in SSH_FAMILIES:
                raise ConnectorError('permission', f'AstDB family {family} is not readable over SSH', 'ssh')
        if missing:
            found = parse_astdb(self._run('astdb ' + ' '.join(missing)))
            for family in missing:
                self._astdb[family] = found.get(family, {})

    def db_get(self, family, key):
        if not (_DB_NAME.match(str(family)) and _DB_NAME.match(str(key))):
            raise ValueError(f'invalid AstDB name {family!r}/{key!r}')
        self.prefetch([family])
        return self._astdb[family].get(str(key))

    def db_get_many(self, family, keys):
        self.prefetch([family])
        values = self._astdb[family]
        return {str(k): values[str(k)] for k in keys if str(k) in values}

    def core_status(self):
        text = self._run('core')
        uptime = re.search(r'System uptime:\s*(\d+)', text)
        reload = re.search(r'Last reload:\s*(\d+)', text)
        calls = re.search(r'(\d+)\s+active calls?', text)
        now = datetime.now().replace(microsecond=0)

        def ago(match):
            return (now - timedelta(seconds=int(match.group(1)))).strftime('%Y-%m-%d %H:%M:%S') if match else None
        return {'startup': ago(uptime), 'reload': ago(reload), 'current_calls': int(calls.group(1)) if calls else 0}

    def driver(self):
        if self.settings.sip_driver in ('sip', 'pjsip'):
            return self.settings.sip_driver
        if self._driver is None:
            found = re.search(r'driver=(\w+)', self._run('driver'))
            if not found or found.group(1) not in ('sip', 'pjsip'):
                raise ConnectorError('driver', 'neither chan_sip nor res_pjsip is loaded', 'ssh')
            self._driver = found.group(1)
        return self._driver

    def peer_names(self):
        return list(parse_peer_listing(self._run('peers')))

    def registry(self):
        text = self._run('registry')
        found = re.search(r'driver=(\w+)', text)
        driver = found.group(1) if found and found.group(1) in ('sip', 'pjsip') else self.driver()
        return driver, parse_registry_text(driver, text)

    def system(self):
        return parse_system(self._run('system'))


class SshWriter:
    """The SSH twin of AmiWriter: uses the separate write key, whose forced command allows only the
    day / night switch and the HOTELTEST probe. No retry."""

    def __init__(self, settings, runner=None):
        self.ssh = settings.ssh
        self._run = runner or SshRunner(self.ssh).run

    def _write(self, request, expected):
        try:
            out = self._run(request, write=True)
        except ConnectorError as exc:
            exc.completed = 0
            raise
        for count, marker in enumerate(expected):
            if marker not in out:
                error = ConnectorError('protocol', f'hotel-pbx did not confirm "{marker}": {" ".join(out.split())[:200]}', 'ssh-write')
                error.completed = count
                raise error
        return len(expected)

    def set_daynight(self, index, state, method='setvar', devstate=True):
        pbx_daynight.write_actions(index, state, 'setvar', devstate)  # same validation as the AMI path
        return self._write(f'daynight-set {index} {state} {1 if devstate else 0}', ['OK state'] + (['OK blf'] if devstate else []))

    def set_probe(self, value, method='setvar'):
        pbx_daynight.test_actions('setvar', value)
        return self._write(f'probe-set {value}', ['OK probe'])


class SshPeerSource:
    """Phone states for the existing phone sync (PBX_SOURCE=ssh), with the web scraper's interface.
    Errors are AMIError, so a lost PBX is handled exactly like the other sources (PBX-LOST)."""

    def __init__(self, settings, runner=None):
        self.ssh = settings.ssh
        self._run = runner or SshRunner(self.ssh).run

    def fetch_peer_states(self):
        try:
            text = self._run('peers')
        except ConnectorError as exc:
            raise AMIError(f'ssh {exc.kind}: {exc.detail}') from exc
        peers = parse_peer_listing(text)
        if not peers:
            raise AMIError('ssh: the PBX listed no SIP peers')
        return peers


def open_reader(settings):
    """A reader for the configured link (use as a context manager)."""
    if settings.link == 'ssh':
        return SshReader(settings)
    if settings.link == 'ami':
        return AmiReader(settings)
    raise ConnectorError('config', 'neither AMI (PBX_AMI_*) nor SSH (PBX_SSH_*) is configured', 'pbx')


def open_writer(settings):
    """The writer for the configured link; the only way the app changes anything on the PBX."""
    if settings.link == 'ssh':
        return SshWriter(settings)
    if settings.link == 'ami':
        return AmiWriter(settings)
    error = ConnectorError('config', 'neither AMI nor SSH is configured', 'pbx')
    error.completed = 0
    raise error


def forget_detected_driver():
    _driver_cache.clear()


# ---- CDR (read-only MySQL) -----------------------------------------------------------------------
_SELECT_ONLY = re.compile(r'^\s*SELECT\s', re.IGNORECASE)
_MYSQL_KINDS = {  # MySQL / MariaDB error number -> kind
    1045: 'auth', 1044: 'permission', 1142: 'permission', 1143: 'permission', 1226: 'permission',
    2002: 'unreachable', 2003: 'unreachable', 2005: 'unreachable', 2006: 'unreachable', 2013: 'timeout',
}


def _default_connect(settings):
    try:
        import pymysql
        import pymysql.cursors
    except ImportError as exc:
        raise ConnectorError('dependency', 'the "PyMySQL" package is not installed', 'cdr') from exc
    return pymysql.connect(host=settings.host, port=settings.port, user=settings.user, password=settings.password,
                           database=settings.database, charset='utf8', autocommit=True,
                           connect_timeout=settings.timeout, read_timeout=settings.timeout * 2,
                           write_timeout=settings.timeout, cursorclass=pymysql.cursors.DictCursor)


def _mysql_error(exc):
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    kind = _MYSQL_KINDS.get(code)
    if kind is None:
        kind = 'timeout' if 'timed out' in str(exc).lower() else 'protocol'
    return ConnectorError(kind, f'{exc.__class__.__name__} {code or ""}: {exc}'.strip(), 'cdr')


class CdrClient:
    """SELECT-only access to the CDR database. One connection per call, closed right after it."""

    def __init__(self, settings, connect=None):
        self.cdr = settings.cdr if isinstance(settings, Settings) else settings
        self._connect = connect or _default_connect

    def select(self, sql, params=(), max_rows=1000):
        """Rows (dicts) of one parameterized SELECT, never more than max_rows."""
        if not _SELECT_ONLY.match(sql or '') or ';' in sql:
            raise ValueError('CdrClient only runs a single SELECT statement')
        if not self.cdr.ready:
            raise ConnectorError('config', 'PBX_CDR_HOST / PBX_CDR_USER / PBX_CDR_PASS are not all set', 'cdr')
        connection = None
        try:
            connection = self._connect(self.cdr)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchmany(max(1, int(max_rows))))
        except ConnectorError:
            raise
        except OSError as exc:
            raise ConnectorError('timeout' if 'timed out' in str(exc).lower() else 'unreachable', str(exc), 'cdr') from exc
        except Exception as exc:  # PyMySQL's own errors (imported lazily, so matched here)
            if exc.__class__.__module__.startswith('pymysql'):
                raise _mysql_error(exc) from exc
            raise
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # closing a dead connection must not hide the real error
                    pass

    def ping(self):
        """Server version, server clock and the time of the newest call; proves SELECT on cdr works."""
        info = self.select('SELECT VERSION() AS version, NOW() AS now', max_rows=1)[0]
        latest = self.select('SELECT calldate FROM cdr ORDER BY calldate DESC LIMIT 1', max_rows=1)
        info['latest_call'] = latest[0]['calldate'] if latest else None
        return info
