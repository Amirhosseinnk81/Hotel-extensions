"""The one place the newer PBX features (call report, DND / forward, health, day / night) talk to Issabel.

Pure connection code: no Flask, no database. Every feature goes through these classes instead of opening
its own sockets, so timeouts, error wording and secret handling live in one file.

  AmiReader  - read-only AMI session (DBGet, CoreStatus, trunk registry, SIP driver detection).
               One short-lived connection per snapshot, opened with `with AmiReader(settings) as ami:`.
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
"""
import os
import re
from dataclasses import dataclass, field

from pbx_sync import AMIClient, AMIError

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
class Settings:
    ami: AmiSettings
    cdr: CdrSettings
    sip_driver: str = 'auto'
    snapshot_ttl: int = 120
    ami_write: AmiSettings = field(default_factory=AmiSettings)  # separate user, only for switching day / night


SIP_DRIVERS = ('auto', 'sip', 'pjsip')
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
