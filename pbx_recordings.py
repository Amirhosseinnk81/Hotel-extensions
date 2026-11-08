"""Call recordings made (legally) by Issabel: which ones may be fetched, from where, and how often.

The app never stores a recording and never builds a public link. A recording is named only by the
CDR uniqueid of its call; the file name is read from the CDR (recordingfile), checked here, and the
audio is streamed through the app from Issabel's own Monitoring download address with the logged-in
panel session (see app.py). Nothing can be deleted or changed from the app.

Pure logic: no Flask, no database, no network.
"""
import posixpath
import re
import threading
import time
from collections import deque
from urllib.parse import quote

MONITOR_ROOT = '/var/spool/asterisk/monitor'
# Issabel Monitoring module download address; {uniqueid} and {file} are URL-encoded before use.
# To be confirmed against the real panel (DevTools -> Network), overridable with PBX_REC_DOWNLOAD_PATH.
DEFAULT_DOWNLOAD_PATH = '/index.php?menu=monitoring&action=download&id={uniqueid}&namefile={file}&rawmode=yes'
AUDIO_EXTENSIONS = ('wav', 'gsm', 'mp3', 'ogg', 'wav49')
_UNIQUEID = re.compile(r'^[0-9]{1,12}\.[0-9]{1,8}$')
_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.+\-]{0,199}$')
_DATE_DIRS = re.compile(r'^[0-9]{4}/[0-9]{2}/[0-9]{2}$')
_DIGITS = re.compile(r'[0-9]')


def valid_uniqueid(value):
    return isinstance(value, str) and bool(_UNIQUEID.match(value))


def safe_recording_name(recordingfile):
    """The recordingfile column -> a name relative to the monitor folder ('x.wav' or '2026/11/08/x.wav'), or None.

    Accepted: a plain file name with an audio extension, optionally below YYYY/MM/DD folders, or the same
    as an absolute path inside /var/spool/asterisk/monitor. Anything else (.., other folders, odd characters,
    other extensions) is refused, so the value can never point outside the recordings folder."""
    text = str(recordingfile or '').strip()
    if not text or '\\' in text or '\x00' in text or '%' in text or '..' in text:
        return None
    if text.startswith('/'):
        normal = posixpath.normpath(text)
        if not normal.startswith(MONITOR_ROOT + '/'):
            return None
        text = normal[len(MONITOR_ROOT) + 1:]
    folder, _, name = text.rpartition('/')
    if folder and not _DATE_DIRS.match(folder):
        return None
    if not _NAME.match(name) or name.rpartition('.')[2].lower() not in AUDIO_EXTENSIONS:
        return None
    return text


def download_path(template, uniqueid, name):
    """Issabel address for one recording; both values are checked and URL-encoded."""
    if not valid_uniqueid(uniqueid) or safe_recording_name(name) != name:
        raise ValueError('refusing to build a recording address from unchecked values')
    return (template or DEFAULT_DOWNLOAD_PATH).format(uniqueid=quote(uniqueid, safe=''), file=quote(name, safe=''))


def mask_number(number):
    """Outside numbers in the activity log: '09121234567' -> '0912*****67'. Extensions (up to 6 digits) stay."""
    text = str(number or '').strip()
    digits = len(_DIGITS.findall(text))
    if digits <= 6:
        return text
    return text[:4] + '*' * (len(text) - 6) + text[-2:]


def download_name(name):
    """File name offered to the browser: the recording's own name, letters / digits / . _ - only."""
    base = name.rpartition('/')[2]
    return re.sub(r'[^A-Za-z0-9_.\-]', '_', base)[:120] or 'recording.wav'


_RANGE = re.compile(r'^bytes=([0-9]{0,15})-([0-9]{0,15})$')


def clean_range(header):
    """A single-range Range header worth passing on, else None (multi-ranges and junk are dropped)."""
    match = _RANGE.match(str(header or '').strip())
    if not match or not (match.group(1) or match.group(2)):
        return None
    return match.group(0)


def starts_playback(range_header):
    """True for the request that begins listening (no Range, or from byte 0); later seeks are not logged again."""
    if not range_header:
        return True
    match = _RANGE.match(str(range_header).strip())
    return bool(match) and match.group(1) == '0'


class RateLimiter:
    """At most `limit` events per key in the last `window` seconds (in memory; the app runs as one process)."""

    def __init__(self, limit, window=3600, clock=time.monotonic):
        self.limit, self.window, self._clock = int(limit), window, clock
        self._events = {}
        self._lock = threading.Lock()

    def allow(self, key):
        """Records the event and returns True, or returns False (nothing recorded) when the limit is reached."""
        if self.limit <= 0:
            return True
        now = self._clock()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True
