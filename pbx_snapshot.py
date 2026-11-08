"""Central cache for data read from the PBX in one go (DND / forward, health, trunk state, ...).

Pages never ask the PBX themselves: they show the last snapshot and when it was taken. Snapshots are
refreshed by the existing pbx-sync background thread (refresh_due() after every phone sync), at most once
per TTL each. One module-wide lock makes sure two loaders never talk to the PBX at the same time; a
refresh that finds the lock busy simply keeps the current data.

A failed refresh keeps the last good data and remembers the error, and is not retried before the TTL
has passed again (no hammering of a PBX that is down). Data older than max_age (default 3 x TTL) is
reported as 'unknown', so a page says "نامشخص" instead of showing an old value as if it were current.

Pure Python: no Flask, no database. State lives in this process (the app runs as a single process).
"""
import logging
import threading
import time
from collections import namedtuple
from datetime import datetime

from pbx_connectors import ConnectorError

log = logging.getLogger('hotel.pbx_snapshot')

_pbx_lock = threading.Lock()  # shared by every cache: one PBX conversation at a time

# data: whatever the loader returned (None before the first success); updated_at: UTC time of that success;
# error: ConnectorError of the last attempt, None if it succeeded; state: ok / stale / unknown
Snapshot = namedtuple('Snapshot', 'name data updated_at error state')


class SnapshotCache:
    def __init__(self, name, loader, ttl, max_age=None, min_interval=15, clock=time.monotonic, utcnow=datetime.utcnow):
        self.name, self.loader = name, loader
        self.ttl = max(1, int(ttl))
        self.max_age = int(max_age) if max_age else 3 * self.ttl
        self.min_interval = max(0, int(min_interval))  # floor between two refreshes, even forced ones
        self._clock, self._utcnow = clock, utcnow
        self._data = None
        self._updated_mono = None  # monotonic time of the last success (ages are measured with this)
        self._updated_at = None    # the same moment as UTC, for display
        self._attempt_mono = None
        self._error = None

    def due(self):
        return self._attempt_mono is None or self._clock() - self._attempt_mono >= self.ttl

    def get(self):
        """The current snapshot; never talks to the PBX."""
        if self._updated_mono is None or self._clock() - self._updated_mono > self.max_age:
            state = 'unknown'
        elif self._error is not None:
            state = 'stale'
        else:
            state = 'ok'
        return Snapshot(self.name, self._data if state != 'unknown' else None, self._updated_at, self._error, state)

    def refresh(self, force=False, immediate=False):
        """Run the loader when due (or forced, but not more often than min_interval). Returns get().
        immediate=True also skips that floor: only for a read right after the app itself changed the PBX."""
        now = self._clock()
        recent = not immediate and self._attempt_mono is not None and now - self._attempt_mono < self.min_interval
        if (not force and not immediate and not self.due()) or recent:
            return self.get()
        if not _pbx_lock.acquire(blocking=False):
            return self.get()  # another snapshot is talking to the PBX right now
        try:
            self._attempt_mono = self._clock()
            try:
                data = self.loader()
            except ConnectorError as exc:
                self._error = exc
                log.warning('snapshot %s failed: %s', self.name, exc)
            except Exception as exc:  # a bug in a loader must not stop the sync thread
                self._error = ConnectorError('protocol', f'{exc.__class__.__name__}: {exc}', self.name)
                log.exception('snapshot %s crashed', self.name)
            else:
                self._data, self._error = data, None
                self._updated_mono, self._updated_at = self._clock(), self._utcnow()
        finally:
            _pbx_lock.release()
        return self.get()


_caches = {}


def register(name, loader, ttl, **options):
    """Create (or replace) the named snapshot. Call once at start-up per feature."""
    _caches[name] = SnapshotCache(name, loader, ttl, **options)
    return _caches[name]


def get(name):
    cache = _caches.get(name)
    return cache.get() if cache else Snapshot(name, None, None, None, 'unknown')


def refresh(name, force=False, immediate=False):
    cache = _caches.get(name)
    return cache.refresh(force, immediate) if cache else get(name)


def refresh_due():
    """Refresh every snapshot whose TTL has passed. Called by the pbx-sync thread; never raises."""
    for cache in list(_caches.values()):
        try:
            cache.refresh()
        except Exception:  # pragma: no cover - refresh() already catches loader errors
            log.exception('snapshot %s refresh failed', cache.name)


def names():
    return set(_caches)


def forget(name):
    _caches.pop(name, None)


def clear():
    """Forget every snapshot (tests, or a settings change)."""
    _caches.clear()
