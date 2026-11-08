"""Call report from Issabel's CDR table (asteriskcdrdb.cdr): filters, SQL and statistics.

Pure logic, no Flask or database: app.py hands the SQL built here to pbx_connectors.CdrClient and the
rows back to summarize(). Nothing from the CDR is stored in the app's database.

Rules (all overridable in .env, see .env.example):
- A date range is mandatory (default: today) and at most PBX_CDR_MAX_DAYS days long, so every query
  can use the index on calldate. Dates are typed in Jalali (1405/08/17) or Gregorian (2026-11-08).
- calldate is the PBX's local time. When PBX_TIMEZONE differs from APP_TIMEZONE the range and the
  shown times are converted; normally both are Asia/Tehran and nothing changes.
- Direction (FreePBX conventions, to be confirmed against the real CDR):
    inbound  = dcontext is one of PBX_CDR_INBOUND_CONTEXTS (from-trunk, from-pstn, ...)
    internal = not inbound and dst looks like an extension (PBX_CDR_INTERNAL_PATTERN)
    outbound = everything else (an extension dialled an outside number)
- Status: disposition ANSWERED / NO ANSWER / BUSY / FAILED (+ CONGESTION counts as failed).
- One call can leave several CDR rows (ring groups, transfers). The table shows the rows; the
  statistics count distinct calls (uniqueid) so a ring group does not count as five calls.
Every value from the user reaches MySQL as a parameter, never inside the SQL text.
"""
import re
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import i18n

DIRECTIONS = ('inbound', 'outbound', 'internal')
STATUSES = ('answered', 'missed', 'busy', 'failed')
DIRECTION_LABELS = {'inbound': 'تماس ورودی', 'outbound': 'تماس خروجی', 'internal': 'تماس داخلی'}
STATUS_LABELS = {'answered': 'پاسخ‌داده', 'missed': 'بی‌پاسخ', 'busy': 'اشغال', 'failed': 'ناموفق'}
_DISPOSITIONS = {'answered': ('ANSWERED',), 'missed': ('NO ANSWER',), 'busy': ('BUSY',), 'failed': ('FAILED', 'CONGESTION')}
QUICK_RANGES = ('today', 'yesterday', '7d', '30d')
PAGE_SIZE = 50
_EXTENSION = re.compile(r'^[0-9]{1,6}$')
_NUMBER = re.compile(r'^[0-9]{3,20}$')
_ASCII = {ord(c): str(i) for i, c in enumerate('۰۱۲۳۴۵۶۷۸۹')}
_ASCII.update({ord(c): str(i) for i, c in enumerate('٠١٢٣٤٥٦٧٨٩')})
_DAY = re.compile(r'^\s*(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*$')
_COLUMNS = 'calldate, src, dst, dcontext, channel, dstchannel, lastapp, disposition, duration, billsec, uniqueid'


# ---- settings ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rules:
    inbound_contexts: tuple = ('from-trunk', 'from-pstn', 'from-did-direct', 'ext-did')
    internal_pattern: str = '^[0-9]{2,5}$'
    reception: tuple = ()  # extensions whose answer rate is shown as "reception"
    max_days: int = 31
    max_rows: int = 5000   # newest rows reachable by paging
    export_rows: int = 20000
    app_tz: object = field(default_factory=lambda: ZoneInfo('Asia/Tehran'))
    pbx_tz: object = field(default_factory=lambda: ZoneInfo('Asia/Tehran'))


def _zone(name, fallback):
    try:
        return ZoneInfo(name) if name else fallback
    except (ZoneInfoNotFoundError, ValueError):
        return fallback


def load_rules(environ, app_tz):
    def text(name, default=''):
        return (environ.get(name) or default).strip()

    def number(name, default, minimum, maximum):
        try:
            value = int(text(name) or default)
        except ValueError:
            value = default
        return min(maximum, max(minimum, value))

    def listing(name, default):
        items = tuple(p.strip() for p in text(name).split(',') if p.strip())
        return items or default

    pattern = text('PBX_CDR_INTERNAL_PATTERN') or Rules.internal_pattern
    try:
        re.compile(pattern)
    except re.error:
        pattern = Rules.internal_pattern
    return Rules(inbound_contexts=listing('PBX_CDR_INBOUND_CONTEXTS', Rules.inbound_contexts), internal_pattern=pattern,
                 reception=tuple(x for x in listing('PBX_CDR_RECEPTION', ()) if _EXTENSION.match(x)),
                 max_days=number('PBX_CDR_MAX_DAYS', 31, 1, 366), max_rows=number('PBX_CDR_MAX_ROWS', 5000, PAGE_SIZE, 100000),
                 export_rows=number('PBX_CDR_EXPORT_ROWS', 20000, 100, 200000),
                 app_tz=app_tz, pbx_tz=_zone(text('PBX_TIMEZONE'), app_tz))


# ---- dates ---------------------------------------------------------------------------------------
def parse_day(value):
    """'1405/08/17', '۱۴۰۵-۰۸-۱۷' (Jalali, year < 1700) or '2026-11-08' (Gregorian) -> date, else None."""
    match = _DAY.match(str(value or '').translate(_ASCII))
    if not match:
        return None
    y, m, d = (int(g) for g in match.groups())
    try:
        if y < 1700:
            if not i18n.jalali_is_valid(y, m, d):
                return None
            return date(*i18n.jalali_to_gregorian(y, m, d))
        return date(y, m, d)
    except ValueError:
        return None


def jalali_text(day):
    if day is None:
        return ''
    y, m, d = i18n.gregorian_to_jalali(day.year, day.month, day.day)
    return f'{y:04d}/{m:02d}/{d:02d}'


def format_call_time(value, rules, jalali=True):
    """calldate (naive, PBX local time) -> 'YYYY/MM/DD HH:MM:SS' in the hotel's time zone."""
    if value is None:
        return ''
    if rules.pbx_tz != rules.app_tz:
        value = value.replace(tzinfo=rules.pbx_tz).astimezone(rules.app_tz).replace(tzinfo=None)
    day = jalali_text(value.date()) if jalali else value.strftime('%Y/%m/%d')
    return f'{day} {value:%H:%M:%S}'


def pbx_bounds(day_from, day_to, rules):
    """Hotel calendar days -> [start, end) as naive PBX-local datetimes (what calldate holds)."""
    start = datetime(day_from.year, day_from.month, day_from.day)
    end = datetime(day_to.year, day_to.month, day_to.day) + timedelta(days=1)
    if rules.pbx_tz != rules.app_tz:
        start, end = (v.replace(tzinfo=rules.app_tz).astimezone(rules.pbx_tz).replace(tzinfo=None) for v in (start, end))
    return start, end


def hotel_today(rules, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(rules.app_tz).date()


# ---- filters -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Filters:
    day_from: date
    day_to: date
    extension: str = ''
    direction: str = ''
    status: str = ''
    page: int = 1
    number: str = ''  # digits of an outside number (caller or dialled), matched anywhere in src / dst

    def as_args(self):
        """Query-string form (Jalali dates), for links and the CSV button."""
        args = {'from': jalali_text(self.day_from), 'to': jalali_text(self.day_to)}
        for key in ('extension', 'direction', 'status', 'number'):
            if getattr(self, key):
                args[key] = getattr(self, key)
        return args


def parse_filters(args, rules, today):
    """Request args -> (Filters, [Persian warning, ...]). Never fails: bad input falls back to a safe value."""
    warnings = []
    quick = (args.get('range') or '').strip()
    if quick in QUICK_RANGES:
        span = {'today': 0, 'yesterday': 0, '7d': 6, '30d': 29}[quick]
        day_to = today - timedelta(days=1) if quick == 'yesterday' else today
        day_from = day_to - timedelta(days=span)
    else:
        raw_from, raw_to = args.get('from', ''), args.get('to', '')
        day_from, day_to = parse_day(raw_from), parse_day(raw_to)
        if (raw_from.strip() and day_from is None) or (raw_to.strip() and day_to is None):
            warnings.append('تاریخ واردشده معتبر نیست؛ به شکل ۱۴۰۵/۰۸/۱۷ بنویسید.')
        day_from = day_from or day_to or today
        day_to = day_to or day_from
    if day_from > day_to:
        day_from, day_to = day_to, day_from
    if (day_to - day_from).days + 1 > rules.max_days:
        day_from = day_to - timedelta(days=rules.max_days - 1)
        warnings.append(f'بازه‌ی تاریخ حداکثر {i18n_digits(rules.max_days)} روز است؛ ابتدای بازه جابه‌جا شد.')
    extension = (args.get('extension') or '').translate(_ASCII).strip()
    if extension and not _EXTENSION.match(extension):
        warnings.append('شماره‌ی داخلی فقط می‌تواند رقم باشد.')
        extension = ''
    number = (args.get('number') or '').translate(_ASCII).strip().lstrip('+')
    if number and not _NUMBER.match(number):
        warnings.append('شماره‌ی تماس فقط می‌تواند رقم باشد (۳ تا ۲۰ رقم).')
        number = ''
    direction = args.get('direction', '') if args.get('direction') in DIRECTIONS else ''
    status = args.get('status', '') if args.get('status') in STATUSES else ''
    try:
        page = int(args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), max_page(rules))
    return Filters(day_from, day_to, extension, direction, status, page, number), warnings


def i18n_digits(value):
    return str(value).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))


def max_page(rules):
    return max(1, rules.max_rows // PAGE_SIZE)


# ---- SQL -----------------------------------------------------------------------------------------
def _in(values):
    return ', '.join(['%s'] * len(values))


def direction_sql(direction, rules):
    inbound = f'dcontext IN ({_in(rules.inbound_contexts)})'
    if direction == 'inbound':
        return inbound, list(rules.inbound_contexts)
    if direction == 'internal':
        return f'NOT ({inbound}) AND dst REGEXP %s', list(rules.inbound_contexts) + [rules.internal_pattern]
    return f'NOT ({inbound}) AND dst NOT REGEXP %s', list(rules.inbound_contexts) + [rules.internal_pattern]


def where_clause(filters, rules):
    """(sql, params) for every filter; the date range always comes first (index on calldate)."""
    start, end = pbx_bounds(filters.day_from, filters.day_to, rules)
    parts, params = ['calldate >= %s AND calldate < %s'], [start, end]
    if filters.extension:
        # the extension as caller, as dialled number, or as either channel (e.g. SIP/101-00000a1b)
        parts.append('(src = %s OR dst = %s OR channel LIKE %s OR dstchannel LIKE %s)')
        pattern = f'%/{filters.extension}-%'
        params += [filters.extension, filters.extension, pattern, pattern]
    if filters.number:
        parts.append('(src LIKE %s OR dst LIKE %s)')
        params += [f'%{filters.number}%'] * 2
    if filters.direction:
        sql, extra = direction_sql(filters.direction, rules)
        parts.append(f'({sql})')
        params += extra
    if filters.status:
        values = _DISPOSITIONS[filters.status]
        parts.append(f'disposition IN ({_in(values)})')
        params += list(values)
    return ' AND '.join(parts), params


def page_query(filters, rules):
    where, params = where_clause(filters, rules)
    offset = (filters.page - 1) * PAGE_SIZE
    return (f'SELECT {_COLUMNS} FROM cdr WHERE {where} ORDER BY calldate DESC, uniqueid DESC LIMIT %s OFFSET %s',
            params + [PAGE_SIZE, offset])


def recordings_query(filters, rules):
    """One page of calls that have a recording (same filters as the call report)."""
    where, params = where_clause(filters, rules)
    offset = (filters.page - 1) * PAGE_SIZE
    return (f"SELECT {_COLUMNS}, recordingfile FROM cdr WHERE {where} AND recordingfile <> '' "
            'ORDER BY calldate DESC, uniqueid DESC LIMIT %s OFFSET %s', params + [PAGE_SIZE, offset])


def recordings_count_query(filters, rules):
    where, params = where_clause(filters, rules)
    return f"SELECT COUNT(*) AS n FROM cdr WHERE {where} AND recordingfile <> ''", params


RECORDING_SQL = ("SELECT calldate, src, dst, billsec, uniqueid, recordingfile FROM cdr "
                 "WHERE uniqueid = %s AND recordingfile <> '' ORDER BY calldate DESC LIMIT 1")


def export_query(filters, rules):
    where, params = where_clause(filters, rules)
    return f'SELECT {_COLUMNS} FROM cdr WHERE {where} ORDER BY calldate DESC, uniqueid DESC LIMIT %s', params + [rules.export_rows]


def count_query(filters, rules):
    where, params = where_clause(filters, rules)
    return f'SELECT COUNT(*) AS n FROM cdr WHERE {where}', params


def stats_queries(filters, rules):
    """{name: (sql, params)} for the summary cards; every one carries the same filters."""
    where, params = where_clause(filters, rules)
    inbound_sql, inbound_params = direction_sql('inbound', rules)
    queries = {
        'totals': ("SELECT COUNT(DISTINCT uniqueid) AS calls, "
                   "COUNT(DISTINCT CASE WHEN disposition = 'ANSWERED' THEN uniqueid END) AS answered "
                   f'FROM cdr WHERE {where}', params),
        'hours': (f'SELECT HOUR(calldate) AS hour, COUNT(DISTINCT uniqueid) AS calls FROM cdr WHERE {where} GROUP BY HOUR(calldate)', params),
        'top': ('SELECT ext, COUNT(*) AS calls FROM ('
                f'SELECT src AS ext FROM cdr WHERE {where} AND src REGEXP %s '
                f'UNION ALL SELECT dst AS ext FROM cdr WHERE {where} AND dst REGEXP %s'
                ') AS legs GROUP BY ext ORDER BY calls DESC, ext LIMIT 10',
                params + [rules.internal_pattern] + params + [rules.internal_pattern]),
    }
    if rules.reception:
        queries['reception'] = ("SELECT COUNT(DISTINCT uniqueid) AS calls, "
                                "COUNT(DISTINCT CASE WHEN disposition = 'ANSWERED' THEN uniqueid END) AS answered "
                                f'FROM cdr WHERE {where} AND ({inbound_sql}) AND dst IN ({_in(rules.reception)})',
                                params + inbound_params + list(rules.reception))
    return queries


# ---- rows and statistics -------------------------------------------------------------------------
def status_of(disposition):
    value = (disposition or '').strip().upper()
    for status, values in _DISPOSITIONS.items():
        if value in values:
            return status
    return ''


def direction_of(row, rules):
    if (row.get('dcontext') or '') in rules.inbound_contexts:
        return 'inbound'
    return 'internal' if re.search(rules.internal_pattern, str(row.get('dst') or '')) else 'outbound'


def present_row(row, rules, jalali=True):
    """A CDR row as the table / CSV shows it."""
    return {'time': format_call_time(row.get('calldate'), rules, jalali), 'src': str(row.get('src') or ''),
            'dst': str(row.get('dst') or ''), 'direction': direction_of(row, rules), 'status': status_of(row.get('disposition')),
            'disposition': row.get('disposition') or '', 'duration': int(row.get('duration') or 0),
            'billsec': int(row.get('billsec') or 0), 'lastapp': row.get('lastapp') or '', 'uniqueid': row.get('uniqueid') or ''}


def rate(answered, calls):
    return round(100.0 * answered / calls, 1) if calls else None


def summarize(results, rules):
    """Raw answers of stats_queries -> what the summary cards show."""
    totals = (results.get('totals') or [{}])[0]
    calls, answered = int(totals.get('calls') or 0), int(totals.get('answered') or 0)
    hours = {int(r['hour']): int(r['calls']) for r in results.get('hours') or [] if r.get('hour') is not None}
    peak = max(hours.values()) if hours else 0
    summary = {
        'calls': calls, 'answered': answered, 'unanswered': calls - answered, 'answer_rate': rate(answered, calls),
        'hours': [{'hour': h, 'calls': hours.get(h, 0), 'width': round(100 * hours.get(h, 0) / peak) if peak else 0} for h in range(24)],
        'peak_hours': sorted(h for h, n in hours.items() if peak and n == peak),
        'top': [{'extension': str(r['ext']), 'calls': int(r['calls'])} for r in results.get('top') or []],
        'reception': None,
    }
    if 'reception' in results:
        row = (results['reception'] or [{}])[0]
        r_calls, r_answered = int(row.get('calls') or 0), int(row.get('answered') or 0)
        summary['reception'] = {'calls': r_calls, 'answered': r_answered, 'rate': rate(r_answered, r_calls)}
    return summary


def format_seconds(seconds):
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'


# ---- short cache for the statistics --------------------------------------------------------------
class TTLCache:
    """A few seconds of memory for identical statistics requests (paging does not re-run them)."""

    def __init__(self, ttl=60, size=64, clock=time.monotonic):
        self.ttl, self.size, self._clock = ttl, size, clock
        self._items = {}

    def get(self, key):
        item = self._items.get(key)
        if item and self._clock() - item[0] < self.ttl:
            return item[1]
        self._items.pop(key, None)
        return None

    def put(self, key, value):
        if len(self._items) >= self.size:
            self._items.pop(min(self._items, key=lambda k: self._items[k][0]))
        self._items[key] = (self._clock(), value)

    def clear(self):
        self._items.clear()


def cache_key(filters):
    return replace(filters, page=1)
