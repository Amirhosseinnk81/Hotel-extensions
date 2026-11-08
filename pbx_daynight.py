"""Day / night mode (IssabelPBX "Call Flow Control", FreePBX 2.11 daynight module): reading and switching.

How IssabelPBX 2.11 keeps it (to be confirmed on the real PBX with
`asterisk -rx "dialplan show app-daynight-toggle"`, see DEPLOYMENT.md section 11-11):
  AstDB DAYNIGHT/C<n> = DAY | NIGHT        n = 0..9, the Call Flow index; empty / missing means DAY
  Custom:DAYNIGHT<n> device state           NOT_INUSE = day, INUSE = night (the BLF lamp), when USEDEVSTATE is on
  feature code *28<n>                       what a phone dials to toggle it (the official way)
  asterisk.daynight table                   ext = n, dmode fc_description / day / night (+ password: never read)
Incoming calls are routed by the AstDB value at the moment they arrive, so writing that key is the switch.

Switching (switch() below): only whitelisted indexes, a fresh read must still show the state the user
confirmed, one write with the separate write-only AMI user, then a fresh read must show the new state.
No automatic retry of a write, ever. Pure logic: the AMI readers / writer are handed in.
"""
import re

SNAPSHOT_NAME = 'daynight'
INDEXES = range(10)
STATES = ('DAY', 'NIGHT')
STATE_LABELS = {'DAY': 'روز', 'NIGHT': 'شب'}
DEVSTATE = {'DAY': 'NOT_INUSE', 'NIGHT': 'INUSE'}
WRITE_METHODS = ('setvar', 'dbput')
ITEMS_SQL = "SELECT ext, dmode, dest FROM {table} WHERE dmode IN ('fc_description', 'day', 'night')"  # never the password row
TC_SQL = 'SELECT timeconditions_id, displayname FROM asterisk.timeconditions'
_TABLE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$')


class SwitchError(Exception):
    """A switch that did not happen (or could not be confirmed). .fa is the Persian text for the page."""

    def __init__(self, fa, detail='', written=False):
        super().__init__(detail or fa)
        self.fa, self.written = fa, written


def valid_index(index):
    return isinstance(index, int) and index in INDEXES


def safe_table(name):
    return name if name and _TABLE.match(name) else 'asterisk.daynight'


def parse_whitelist(text):
    """'0,1' -> {0, 1}; anything that is not an index 0..9 is ignored."""
    found = set()
    for part in str(text or '').split(','):
        part = part.strip()
        if part.isdigit() and int(part) in INDEXES:
            found.add(int(part))
    return found


def parse_labels(text):
    """PBX_DAYNIGHT_LABELS '0:پذیرش,1:رستوران' -> {0: 'پذیرش', 1: 'رستوران'} (names when the table is not readable)."""
    labels = {}
    for part in str(text or '').split(','):
        index, _, name = part.partition(':')
        if index.strip().isdigit() and int(index) in INDEXES and name.strip():
            labels[int(index)] = name.strip()[:80]
    return labels


def parse_items(rows):
    """daynight table rows -> {index: {'name', 'day', 'night'}}."""
    items = {}
    for row in rows:
        try:
            index = int(str(row.get('ext')).strip())
        except (TypeError, ValueError):
            continue
        if index not in INDEXES:
            continue
        item = items.setdefault(index, {'name': '', 'day': '', 'night': ''})
        mode, dest = str(row.get('dmode') or ''), ' '.join(str(row.get('dest') or '').split())[:120]
        if mode == 'fc_description':
            item['name'] = dest
        elif mode in ('day', 'night'):
            item[mode] = dest
    return items


_DEST_KINDS = (
    (re.compile(r'^from-did-direct,(\d+),'), 'داخلی {0}'),
    (re.compile(r'^ext-local,vm[bus]?(\d+),'), 'صندوق صوتی {0}'),
    (re.compile(r'^ext-group,(\d+),'), 'گروه زنگ {0}'),
    (re.compile(r'^ext-queues,(\d+),'), 'صف {0}'),
    (re.compile(r'^ivr-(\d+),'), 'منوی صوتی (IVR) {0}'),
    (re.compile(r'^timeconditions,(\d+),'), 'شرط زمانی {0}'),
    (re.compile(r'^app-announcement-(\d+),'), 'اعلان {0}'),
    (re.compile(r'^app-daynight,(\d+),'), 'کنترل روز/شب {0}'),
    (re.compile(r'^ext-meetme,(\d+),'), 'کنفرانس {0}'),
    (re.compile(r'^app-blackhole,hangup,'), 'قطع تماس'),
    (re.compile(r'^app-blackhole,busy,'), 'بوق اشغال'),
)


def describe_destination(dest):
    """'ext-group,600,1' -> 'گروه زنگ 600'; unknown forms are shown as they are; empty -> ''."""
    dest = str(dest or '').strip()
    for pattern, label in _DEST_KINDS:
        match = pattern.match(dest)
        if match:
            return label.format(*match.groups())
    return dest


def state_of(value):
    """AstDB value -> 'DAY' / 'NIGHT'. FreePBX treats anything but NIGHT (also a missing key) as day."""
    return 'NIGHT' if str(value or '').strip().upper() == 'NIGHT' else 'DAY'


def read_states(reader, indexes):
    """{index: 'DAY' | 'NIGHT'} from AstDB, plus which indexes had a key at all."""
    states, present = {}, set()
    for index in sorted(indexes):
        value = reader.db_get('DAYNIGHT', f'C{index}')
        if value is not None:
            present.add(index)
        states[index] = state_of(value)
    return states, present


def write_actions(index, target, method='setvar', devstate=True):
    """The AMI actions that switch one Call Flow: [(action, fields), ...]. Validates everything first."""
    if not valid_index(index) or target not in STATES or method not in WRITE_METHODS:
        raise ValueError('refusing to build a day/night write from unchecked values')
    if method == 'dbput':  # needs "write = system" on the write user
        actions = [('DBPut', {'Family': 'DAYNIGHT', 'Key': f'C{index}', 'Val': target})]
    else:  # Setvar without a channel writes the DB() function; needs "write = call"
        actions = [('Setvar', {'Variable': f'DB(DAYNIGHT/C{index})', 'Value': target})]
    if devstate:
        actions.append(('Setvar', {'Variable': f'DEVICE_STATE(Custom:DAYNIGHT{index})', 'Value': DEVSTATE[target]}))
    return actions


TEST_FAMILY, TEST_KEY = 'HOTELTEST', 'probe'


def test_actions(method, value):
    """Same write mechanism, aimed at a harmless key (HOTELTEST/probe) that no dialplan reads."""
    if method not in WRITE_METHODS or not re.match(r'^[A-Za-z0-9]{1,20}$', str(value)):
        raise ValueError('bad test write')
    if method == 'dbput':
        return [('DBPut', {'Family': TEST_FAMILY, 'Key': TEST_KEY, 'Val': value})]
    return [('Setvar', {'Variable': f'DB({TEST_FAMILY}/{TEST_KEY})', 'Value': value})]


def switch(read_state, write, index, expected, target):
    """read_state(index) -> 'DAY' / 'NIGHT' (a fresh read); write(index, target) does the AMI write once.
    Returns the confirmed new state, or raises SwitchError. Never retries the write."""
    if expected not in STATES or target not in STATES or expected == target:
        raise SwitchError('درخواست تعویض معتبر نیست.')
    current = read_state(index)
    if current != expected:
        raise SwitchError(f'وضعیت در این فاصله عوض شده است (الان «{STATE_LABELS[current]}» است)؛ صفحه را دوباره ببینید و اگر هنوز لازم است دوباره تأیید کنید.',
                          f'expected {expected}, found {current}')
    write(index, target)
    try:
        after = read_state(index)
    except Exception as exc:
        raise SwitchError('دستور نوشتن فرستاده شد ولی خواندن دوباره ممکن نشد؛ وضعیت فعلی را در ایزابل بررسی کنید.',
                          f'read-back failed: {exc}', written=True) from exc
    if after != target:
        raise SwitchError(f'دستور نوشتن فرستاده شد ولی ایزابل هنوز «{STATE_LABELS[after]}» نشان می‌دهد؛ تعویض انجام نشد.',
                          f'read-back {after}, wanted {target}', written=True)
    return after
