"""Health of the Issabel server: readings, and alerts with hysteresis (pure logic, no Flask / database).

Where each value comes from (decided in stage 0):
  AMI (read-only user)         uptime (CoreStatus), calls in progress, trunk registrations
  Issabel web dashboard        CPU, memory, swap, disk usage, service states (Asterisk, MySQL, ...)
No shell or "System" action is ever used on the PBX.

Alerts (OutageEvent rows, category 'pbx', shown by the same bell as the phone alerts):
  disk      a partition at or above PBX_HEALTH_DISK_WARN %       cleared below PBX_HEALTH_DISK_CLEAR %
  cpu       CPU at or above PBX_HEALTH_CPU_WARN % for PBX_HEALTH_CPU_SAMPLES readings in a row
            cleared below PBX_HEALTH_CPU_CLEAR %
  asterisk  the dashboard says Asterisk is stopped (twice in a row); cleared when it runs again
  trunk     a trunk is not registered in two AMI readings in a row; cleared (recovery alert) when it is again
Hysteresis = a dead band between the warn and clear values (no change inside it) plus a number of
readings in a row before the state flips, so a value hovering around the limit does not flood the bell.
"Unknown" never changes anything: when the PBX (or one source) cannot be read, no alert is raised or
cleared, the same rule the phone outages follow when the PBX link is lost (PBX-LOST).
"""
import re
from dataclasses import dataclass
from datetime import datetime

SNAPSHOT_NAME = 'pbx_health'
_NUM = r'(\d{1,3}(?:[.,]\d+)?)\s*%'
_CPU = re.compile(r'\bCPU\b[^%\n]{0,60}?' + _NUM, re.IGNORECASE)
_MEMORY = re.compile(r'\b(?:RAM|Memory|Memoria)\b[^%\n]{0,60}?' + _NUM, re.IGNORECASE)
_SWAP = re.compile(r'\bSwap\b[^%\n]{0,60}?' + _NUM, re.IGNORECASE)
_DISK = re.compile(r'(?<![\w/.])(?P<mount>/(?:[A-Za-z][\w/.\-]*)?)(?![\w/])[^%\n]{0,80}?' + _NUM)  # "/" or "/var/spool ..."
_SERVICE = re.compile(r'\b(?P<name>Asterisk|MySQL|MariaDB|Apache|httpd|Postfix|Hylafax|IAXmodem|Dahdi|Openfire|Cron|SSH)\b'
                      r'[^\n]{0,60}?\b(?P<state>Running|Stopped|Not\s+running|Active|Inactive|Activo|Detenido|Up|Down|OK|Failed)\b',
                      re.IGNORECASE)
_RUNNING = {'running', 'active', 'activo', 'up', 'ok'}


@dataclass(frozen=True)
class Thresholds:
    disk_warn: float = 85.0
    disk_clear: float = 80.0
    cpu_warn: float = 90.0
    cpu_clear: float = 75.0
    cpu_samples: int = 3
    alerts: bool = True


def load_thresholds(environ):
    def number(name, default, cast=float, minimum=0, maximum=100):
        try:
            value = cast((environ.get(name) or '').strip() or default)
        except ValueError:
            value = cast(default)
        return min(maximum, max(minimum, value))

    disk_warn, cpu_warn = number('PBX_HEALTH_DISK_WARN', 85), number('PBX_HEALTH_CPU_WARN', 90)
    return Thresholds(disk_warn=disk_warn, disk_clear=min(disk_warn, number('PBX_HEALTH_DISK_CLEAR', 80)),
                      cpu_warn=cpu_warn, cpu_clear=min(cpu_warn, number('PBX_HEALTH_CPU_CLEAR', 75)),
                      cpu_samples=int(number('PBX_HEALTH_CPU_SAMPLES', 3, int, 1, 60)),
                      alerts=(environ.get('PBX_HEALTH_ALERTS') or '1').strip().lower() not in ('0', 'false', 'no', 'off'))


# ---- readings ------------------------------------------------------------------------------------
def _percent(match):
    return float(match.group(match.lastindex).replace(',', '.')) if match else None


def parse_dashboard(text):
    """Text of the Issabel dashboard page(s) -> {'cpu', 'memory', 'swap', 'disks', 'services'}.
    Missing values stay None / empty; the caller treats "nothing found" as an error, not as zero."""
    text = text or ''
    disks, seen = [], set()
    for match in _DISK.finditer(text):
        mount, value = match.group('mount'), float(match.group(2).replace(',', '.'))
        if mount not in seen and value <= 100 and not mount.startswith(('/index.php', '/modules', '/themes')):
            seen.add(mount)
            disks.append({'mount': mount, 'percent': value})
    services = {}
    for match in _SERVICE.finditer(text):
        name = match.group('name').lower()
        name = 'mysql' if name == 'mariadb' else 'apache' if name == 'httpd' else name
        services.setdefault(name, 'running' if match.group('state').lower() in _RUNNING else 'stopped')
    return {'cpu': _percent(_CPU.search(text)), 'memory': _percent(_MEMORY.search(text)), 'swap': _percent(_SWAP.search(text)),
            'disks': disks, 'services': services}


def has_values(dashboard):
    return dashboard is not None and (dashboard['cpu'] is not None or dashboard['memory'] is not None
                                      or dashboard['disks'] or dashboard['services'])


def parse_trunks(driver, events):
    """Registry events (AmiReader.registry) -> [{'name', 'state', 'registered'}], sorted by name."""
    trunks = []
    for event in events:
        if driver == 'pjsip':
            name, state = event.get('ObjectName', ''), event.get('Status', '')
        else:
            user, host = event.get('Username', ''), event.get('Host', '')
            name, state = (f'{user}@{host}' if user else host), event.get('State', '')
        if name:
            trunks.append({'name': name[:120], 'state': state or '?', 'registered': state.strip().lower() == 'registered'})
    return sorted(trunks, key=lambda t: t['name'])


def asterisk_state(ami_ok, services):
    """'running' / 'stopped' / None (unknown). A dead AMI alone proves nothing (network, firewall)."""
    if ami_ok:
        return 'running'
    return (services or {}).get('asterisk')


def uptime_seconds(startup, now_local):
    """CoreStartup 'YYYY-MM-DD HH:MM:SS' (PBX local time) -> seconds, or None."""
    try:
        started = datetime.strptime(str(startup), '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None
    return max(0, int((now_local - started).total_seconds()))


# ---- alerts --------------------------------------------------------------------------------------
@dataclass
class AlertState:
    active: bool = False
    count: int = 0          # readings in a row that point the other way
    since: object = None    # when the current state began (UTC)
    subject: str = ''
    detail: str = ''


RAISE_AFTER = {'disk': 2, 'asterisk': 2, 'trunk': 2}  # cpu: Thresholds.cpu_samples
CLEAR_AFTER = {'disk': 2, 'cpu': 2, 'asterisk': 1, 'trunk': 1}


def kind_of(key):
    return key.split(':', 1)[0]


def observations(reading, thresholds):
    """{key: (bad: True / False / None, subject, detail)} from one health reading.
    None = no opinion (unknown, or inside the dead band): the state stays as it is."""
    found = {}
    dashboard = reading.get('dashboard')
    if dashboard:
        if dashboard['disks']:
            worst = max(dashboard['disks'], key=lambda d: d['percent'])
            value = worst['percent']
            bad = True if value >= thresholds.disk_warn else False if value < thresholds.disk_clear else None
            found['disk'] = (bad, worst['mount'], f'{value:g}%')
        if dashboard['cpu'] is not None:
            value = dashboard['cpu']
            bad = True if value >= thresholds.cpu_warn else False if value < thresholds.cpu_clear else None
            found['cpu'] = (bad, 'CPU', f'{value:g}%')
    state = reading.get('asterisk')
    if state in ('running', 'stopped'):
        found['asterisk'] = (state == 'stopped', 'Asterisk', '')
    if reading.get('ami_ok'):
        for trunk in reading.get('trunks') or []:
            found[f"trunk:{trunk['name']}"] = (not trunk['registered'], trunk['name'], trunk['state'][:40])
    return found


def evaluate(states, found, thresholds, now, ami_ok):
    """Update states (dict key -> AlertState, changed in place) and return the events
    [('raised' | 'cleared', key, subject, detail), ...]. Trunks that vanished from a good AMI reading
    are forgotten quietly; nothing else is touched by a missing observation."""
    events = []
    for key, (bad, subject, detail) in found.items():
        state = states.setdefault(key, AlertState(since=now, subject=subject))
        state.subject, state.detail = subject, detail
        if bad is None or bad == state.active:
            state.count = 0
            continue
        state.count += 1
        kind = kind_of(key)
        needed = (thresholds.cpu_samples if kind == 'cpu' else RAISE_AFTER.get(kind, 2)) if bad else CLEAR_AFTER.get(kind, 1)
        if state.count >= needed:
            state.active, state.count, state.since = bad, 0, now
            events.append(('raised' if bad else 'cleared', key, subject, detail))
    if ami_ok:
        for key in [k for k in states if kind_of(k) == 'trunk' and k not in found]:
            del states[key]  # removed from the PBX configuration: no alert, no recovery
    return events
