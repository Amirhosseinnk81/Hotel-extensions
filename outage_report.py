"""Helpers for the outage report page (pure logic, no Flask / database)."""
from datetime import date, datetime, timedelta, timezone

import outage_monitor

_ASCII_DIGITS = {ord(c): str(i) for i, c in enumerate('۰۱۲۳۴۵۶۷۸۹')}
_ASCII_DIGITS.update({ord(c): str(i) for i, c in enumerate('٠١٢٣٤٥٦٧٨٩')})


def normalize_digits(text):
    """Persian / Arabic digits -> 0-9, so '۲۵۵' finds extension 255."""
    return str(text or '').translate(_ASCII_DIGITS)


def parse_day(value):
    """'2026-10-16' (also with Persian digits) -> date, anything else -> None."""
    try:
        return datetime.strptime(normalize_digits(value).strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _local_midnight_as_utc(day, tz):
    local = datetime(day.year, day.month, day.day, tzinfo=tz)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def day_bounds_utc(day_from, day_to, tz):
    """Local calendar days -> (start, end) as naive UTC datetimes, end exclusive; either may be None.
    The days are swapped when they come in the wrong order."""
    if day_from and day_to and day_from > day_to:
        day_from, day_to = day_to, day_from
    start = _local_midnight_as_utc(day_from, tz) if day_from else None
    end = _local_midnight_as_utc(day_to + timedelta(days=1), tz) if day_to else None
    return start, end


def local_midnight_utc(now_utc, tz):
    """The start of 'today' in the hotel's timezone, as naive UTC."""
    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    return _local_midnight_as_utc(local_now.date(), tz)


def worst_offenders(rows, limit=5):
    """rows: (extension, title, seconds) per outage, oldest first -> the phones that failed most.

    Sorted by number of outages, then total time down, then extension. The title is the most recent one."""
    totals = {}
    for extension, title, seconds in rows:
        item = totals.setdefault(extension, {'extension': extension, 'title': '', 'count': 0, 'seconds': 0})
        item['count'] += 1
        item['seconds'] += max(0, int(seconds or 0))
        if title:
            item['title'] = title
    ranked = sorted(totals.values(), key=lambda item: (-item['count'], -item['seconds'], item['extension']))
    for item in ranked:
        item['duration'] = outage_monitor.format_duration(item['seconds'])
    return ranked[:limit]


def csv_safe(value):
    """A spreadsheet runs a cell that starts with = + - @ as a formula; defuse titles typed by users."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text
