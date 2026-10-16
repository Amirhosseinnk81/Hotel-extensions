"""Phone outage tracking: decides when a phone "went offline" / "came back" and writes the Log file.

Pure logic with no Flask or database imports, so it is easy to test. The caller (app.py) hands in
the currently open outages and two small callables that create / discard database rows.

How a phone is judged
- A phone only counts as having gone offline if it was seen online just before (a phone that was
  already offline when we started watching is not an "outage").
- It must stay offline for `confirm_syncs` syncs in a row (default 2, about a minute) before the outage
  is confirmed and logged; a shorter blip leaves no trace at all.
- An outage ends when the phone is seen online again ("online"), or when the phone is no longer
  monitored: disabled, trashed, removed from the PBX ("stopped").
- "unknown" says nothing about the phone, so it changes nothing.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

OUTAGE_LOGGER_NAME = 'hotel.outage'
LOG_FILE_NAME = 'Log.txt'
_TAG_WIDTH = 8  # OFFLINE / ONLINE / STOPPED / PBX-LOST / PBX-OK are padded to this width

outage_logger = logging.getLogger(OUTAGE_LOGGER_NAME)
outage_logger.setLevel(logging.INFO)
outage_logger.propagate = False  # Log.txt only holds outage lines; app.log stays separate


def setup_log_file(path, max_bytes=5_000_000, backups=5):
    """Send outage lines to `path` (UTF-8 with BOM so Windows Notepad shows Persian correctly).

    Safe to call again: an earlier handler of this logger is closed and replaced, never duplicated.
    The file is only created when the first line is written."""
    for handler in list(outage_logger.handlers):
        outage_logger.removeHandler(handler)
        handler.close()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8-sig', delay=True)
    handler.setFormatter(logging.Formatter('%(message)s'))
    outage_logger.addHandler(handler)
    return handler


def format_duration(seconds):
    """83 -> '00:01:23'; a day or more -> '1d 02:03:04'."""
    seconds = max(0, int(round(seconds)))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    clock = f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{days}d {clock}' if days else clock


def clean(text, limit=200):
    """One line, no field separator: a title or error message cannot break the log format."""
    text = ' '.join(str(text or '').split()).replace('|', '/')
    return (text[:limit - 1] + '…') if len(text) > limit else (text or '-')


def make_line(local_time, tag, extension, ip, detail, title):
    # Latin / numeric fields first and the (Persian) title last, so right-to-left text cannot
    # reshuffle the columns in an editor.
    fields = [local_time, tag.ljust(_TAG_WIDTH), clean(extension), clean(ip), clean(detail), clean(title)]
    return ' | '.join(fields)


def write_pbx_event(tag, detail, local_time):
    """'PBX-LOST' / 'PBX-OK': the link to the PBX itself went down or came back."""
    outage_logger.info(make_line(local_time, tag, '-', '-', detail, '-'))


class OutageTracker:
    """One instance per sync cycle.

    open_outages: {extension: record} of outages that have not ended yet.
    create(**fields) -> record: makes (and stores) a new outage record.
    discard(record): deletes an outage record that never got confirmed.
    to_local(datetime) -> str: formats a UTC time for the log.
    Records need: extension, title, last_ip, started_at, ended_at, end_reason, confirmed, offline_syncs.
    Log lines are collected and written by write_log(), which the caller runs after the database
    commit succeeded, so the file never describes something the database does not hold."""

    def __init__(self, open_outages, create, discard, to_local, confirm_syncs=2):
        self.open = dict(open_outages)
        self._create = create
        self._discard = discard
        self._to_local = to_local
        self.confirm_syncs = max(1, int(confirm_syncs))
        self._lines = []
        self.went_offline = 0
        self.came_online = 0

    def observe(self, extension, state, prior, title, ip, now):
        """One monitored phone seen in this sync.

        state: 'online' / 'offline' / 'unknown' now; prior: its state in the previous sync if that is
        recent enough to trust, else None."""
        record = self.open.get(extension)
        if state == 'offline':
            if record is not None:
                record.offline_syncs += 1
                if ip:
                    record.last_ip = ip
                if not record.confirmed and record.offline_syncs >= self.confirm_syncs:
                    self._confirm(record, now)
            elif prior == 'online':
                record = self._create(extension=extension, title=title, last_ip=ip or None, started_at=now,
                                      ended_at=None, end_reason=None, confirmed=False, offline_syncs=1)
                self.open[extension] = record
                if record.offline_syncs >= self.confirm_syncs:
                    self._confirm(record, now)
        elif state == 'online' and record is not None:
            self._close(record, now, 'online')

    def stop_unobserved(self, observed, now):
        """Close every open outage whose phone was not part of this sync (disabled, trashed, gone)."""
        for extension in list(self.open):
            if extension not in observed:
                self._close(self.open[extension], now, 'stopped')

    def _confirm(self, record, now):
        record.confirmed = True
        self.went_offline += 1
        since = self._to_local(record.started_at)
        self._lines.append(make_line(self._to_local(now), 'OFFLINE', record.extension, record.last_ip,
                                     f'down since {since}', record.title))

    def _close(self, record, now, reason):
        self.open.pop(record.extension, None)
        if not record.confirmed:
            self._discard(record)  # a blip shorter than the confirmation time: not worth a record
            return
        record.ended_at = now
        record.end_reason = reason
        lasted = format_duration((now - record.started_at).total_seconds())
        if reason == 'online':
            self.came_online += 1
            self._lines.append(make_line(self._to_local(now), 'ONLINE', record.extension, record.last_ip,
                                         f'was down {lasted}', record.title))
        else:
            self._lines.append(make_line(self._to_local(now), 'STOPPED', record.extension, record.last_ip,
                                         f'monitoring ended after {lasted} (disabled, removed or gone from the PBX)',
                                         record.title))

    def write_log(self):
        lines, self._lines = self._lines, []
        for line in lines:
            outage_logger.info(line)
        return len(lines)
