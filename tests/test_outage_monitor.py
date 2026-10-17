"""Tests for the outage decision logic and the Log file (no Flask / database needed)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

import outage_monitor as om

T0 = datetime(2026, 10, 16, 10, 0, 0)  # UTC, naive like the database values


def local(dt):  # the app shows local time; the tests only need a stable, readable format
    return dt.strftime('%Y-%m-%d %H:%M:%S')


class Harness:
    """A tracker per sync cycle on top of an in-memory 'table', like app.py does with the database."""

    def __init__(self, confirm_syncs=2):
        self.rows = []
        self.confirm_syncs = confirm_syncs
        self.log = []
        self._handler = None

    def open_rows(self):
        return {r.extension: r for r in self.rows if r.ended_at is None}

    def cycle(self, now, phones, **kwargs):
        """phones: {extension: (state, prior)}; returns the tracker after the cycle."""
        def create(**fields):
            row = SimpleNamespace(**fields)
            self.rows.append(row)
            return row

        tracker = om.OutageTracker(self.open_rows(), create, self.rows.remove, local, self.confirm_syncs)
        for ext, (state, prior) in phones.items():
            tracker.observe(ext, state, prior, f'Title {ext}', '10.0.0.' + ext[-1], now)
        tracker.stop_unobserved(set(phones), now)
        tracker.write_log()
        return tracker


class LogCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'logs', 'Log.txt')
        self.handler = om.setup_log_file(self.path)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for handler in list(om.outage_logger.handlers):
            om.outage_logger.removeHandler(handler)
            handler.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding='utf-8-sig') as f:
            return f.read().splitlines()


class DecisionTests(LogCapture):
    def test_full_outage_cycle_with_duration(self):
        h = Harness()
        h.cycle(T0, {'255': ('online', 'online')})
        h.cycle(T0 + timedelta(seconds=30), {'255': ('offline', 'online')})
        self.assertEqual(self.lines(), [])  # one offline sync is not confirmed yet
        h.cycle(T0 + timedelta(seconds=60), {'255': ('offline', 'offline')})
        self.assertEqual(len(self.lines()), 1)
        self.assertIn('| OFFLINE  | 255 | 10.0.0.5 | down since 2026-10-16 10:00:30 | Title 255', self.lines()[0])
        row = h.rows[0]
        self.assertTrue(row.confirmed)
        self.assertEqual(row.started_at, T0 + timedelta(seconds=30))
        h.cycle(T0 + timedelta(seconds=30 + 761), {'255': ('online', 'offline')})
        self.assertEqual(len(self.lines()), 2)
        self.assertIn('| ONLINE   | 255 | 10.0.0.5 | was down 00:12:41 | Title 255', self.lines()[1])
        self.assertEqual((row.end_reason, row.ended_at), ('online', T0 + timedelta(seconds=791)))

    def test_phone_already_offline_when_first_seen_is_not_an_outage(self):
        h = Harness()
        for i in range(4):
            h.cycle(T0 + timedelta(seconds=30 * i), {'255': ('offline', None)})
            self.assertEqual(h.rows, [])
        h.cycle(T0 + timedelta(seconds=200), {'255': ('online', 'offline')})
        self.assertEqual((h.rows, self.lines()), ([], []))

    def test_blip_leaves_no_trace(self):
        h = Harness()
        h.cycle(T0, {'255': ('offline', 'online')})
        self.assertEqual(len(h.rows), 1)
        h.cycle(T0 + timedelta(seconds=30), {'255': ('online', 'offline')})
        self.assertEqual((h.rows, self.lines()), ([], []))

    def test_confirmation_after_one_sync_when_configured(self):
        h = Harness(confirm_syncs=1)
        tracker = h.cycle(T0, {'255': ('offline', 'online')})
        self.assertEqual((tracker.went_offline, len(self.lines())), (1, 1))

    def test_unknown_changes_nothing(self):
        h = Harness()
        h.cycle(T0, {'255': ('offline', 'online')})
        h.cycle(T0 + timedelta(seconds=30), {'255': ('unknown', 'offline')})
        self.assertEqual(h.rows[0].offline_syncs, 1)
        h.cycle(T0 + timedelta(seconds=60), {'255': ('unknown', 'unknown')})
        self.assertEqual(len(h.rows), 1)
        self.assertIsNone(h.rows[0].ended_at)

    def test_long_outage_stays_one_record(self):
        h = Harness()
        h.cycle(T0, {'255': ('offline', 'online')})
        for i in range(1, 20):
            h.cycle(T0 + timedelta(seconds=30 * i), {'255': ('offline', 'offline')})
        self.assertEqual(len(h.rows), 1)
        self.assertEqual(len(self.lines()), 1)  # logged once, when confirmed

    def test_phone_that_leaves_monitoring_ends_its_outage(self):
        h = Harness()
        h.cycle(T0, {'255': ('offline', 'online'), '256': ('offline', 'online')})
        h.cycle(T0 + timedelta(seconds=30), {'255': ('offline', 'offline'), '256': ('offline', 'offline')})
        self.assertEqual(len(self.lines()), 2)
        # 255 is disabled / trashed: not observed any more
        h.cycle(T0 + timedelta(seconds=90), {'256': ('offline', 'offline')})
        stopped = [line for line in self.lines() if 'STOPPED' in line]
        self.assertEqual(len(stopped), 1)
        self.assertIn('255', stopped[0])
        self.assertIn('monitoring ended after 00:01:30', stopped[0])
        done = [r for r in h.rows if r.extension == '255'][0]
        self.assertEqual(done.end_reason, 'stopped')
        self.assertIsNone([r for r in h.rows if r.extension == '256'][0].ended_at)

    def test_unconfirmed_outage_of_a_phone_that_leaves_is_dropped(self):
        h = Harness()
        h.cycle(T0, {'255': ('offline', 'online')})
        h.cycle(T0 + timedelta(seconds=30), {})
        self.assertEqual((h.rows, self.lines()), ([], []))

    def test_a_second_outage_after_recovery_is_a_new_record(self):
        h = Harness(confirm_syncs=1)
        h.cycle(T0, {'255': ('offline', 'online')})
        h.cycle(T0 + timedelta(minutes=5), {'255': ('online', 'offline')})
        h.cycle(T0 + timedelta(minutes=9), {'255': ('offline', 'online')})
        self.assertEqual(len(h.rows), 2)
        self.assertEqual([r.ended_at is None for r in h.rows], [False, True])

    def test_lines_are_only_written_by_write_log(self):
        h = Harness(confirm_syncs=1)
        tracker = om.OutageTracker({}, lambda **f: SimpleNamespace(**f), lambda r: None, local, 1)
        tracker.observe('255', 'offline', 'online', 'T', '1.1.1.1', T0)
        self.assertEqual(self.lines(), [])
        self.assertEqual(tracker.write_log(), 1)
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(tracker.write_log(), 0)  # nothing is written twice


class FormatTests(LogCapture):
    def test_durations(self):
        self.assertEqual(om.format_duration(0), '00:00:00')
        self.assertEqual(om.format_duration(83), '00:01:23')
        self.assertEqual(om.format_duration(3600 * 5 + 61), '05:01:01')
        self.assertEqual(om.format_duration(86400 + 2 * 3600 + 3 * 60 + 4), '1d 02:03:04')
        self.assertEqual(om.format_duration(-5), '00:00:00')

    def test_one_line_per_event_whatever_the_title_contains(self):
        line = om.make_line('2026-10-16 10:00:00', 'OFFLINE', '255', '1.1.1.1', 'down', 'Front | desk\nsecond\r\nline')
        self.assertNotIn('\n', line)
        self.assertNotIn('\r', line)
        self.assertEqual(line.count(' | '), 5)
        self.assertTrue(line.endswith('Front / desk second line'))

    def test_empty_fields_become_dashes_and_long_text_is_cut(self):
        line = om.make_line('t', 'ONLINE', '255', None, '', 'x' * 500)
        parts = line.split(' | ')
        self.assertEqual(parts[3], '-')
        self.assertEqual(parts[4], '-')
        self.assertLessEqual(len(parts[5]), 200)

    def test_tags_line_up(self):
        a = om.make_line('t', 'ONLINE', '1', '-', 'd', 't').split(' | ')[1]
        b = om.make_line('t', 'PBX-LOST', '1', '-', 'd', 't').split(' | ')[1]
        self.assertEqual(len(a), len(b))

    def test_persian_title_is_written_as_utf8_with_one_bom(self):
        om.write_pbx_event('PBX-LOST', 'cannot connect', '2026-10-16 10:00:00')
        h = Harness(confirm_syncs=1)
        tracker = om.OutageTracker({}, lambda **f: SimpleNamespace(**f), lambda r: None, local, 1)
        tracker.observe('255', 'offline', 'online', 'پذیرش ۳', '1.1.1.1', T0)
        tracker.write_log()
        with open(self.path, 'rb') as f:
            raw = f.read()
        self.assertEqual(raw.count(b'\xef\xbb\xbf'), 1)
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertIn('پذیرش ۳', raw.decode('utf-8-sig'))
        self.assertIn('PBX-LOST', self.lines()[0])

    def test_setup_twice_keeps_a_single_handler(self):
        om.setup_log_file(self.path)
        om.setup_log_file(self.path)
        self.assertEqual(len(om.outage_logger.handlers), 1)
        om.write_pbx_event('PBX-OK', 'x', 't')
        self.assertEqual(len(self.lines()), 1)

    def test_file_is_created_lazily(self):
        self.assertFalse(os.path.exists(self.path))
        om.write_pbx_event('PBX-OK', 'x', 't')
        self.assertTrue(os.path.exists(self.path))

    def test_outage_lines_do_not_leak_into_the_root_logger(self):
        self.assertFalse(om.outage_logger.propagate)


class EmitHookTests(LogCapture):
    """The tracker tells the caller about confirmed outages and recoveries (this feeds the browser alerts)."""

    def run_cycles(self, steps, confirm_syncs=2):
        """steps: [(seconds, {extension: (state, prior)})] -> (events, rows)"""
        events, rows = [], []

        def create(**fields):
            row = SimpleNamespace(**fields)
            rows.append(row)
            return row

        for seconds, phones in steps:
            now = T0 + timedelta(seconds=seconds)
            open_rows = {r.extension: r for r in rows if r.ended_at is None}
            tracker = om.OutageTracker(open_rows, create, rows.remove, local, confirm_syncs,
                                       lambda kind, record, at: events.append((kind, record.extension, at)))
            for ext, (state, prior) in phones.items():
                tracker.observe(ext, state, prior, f'Title {ext}', '10.0.0.' + ext[-1], now)
            tracker.stop_unobserved(set(phones), now)
            tracker.write_log()
        return events, rows

    def test_no_event_for_a_phone_that_was_already_offline(self):
        events, _ = self.run_cycles([(0, {'255': ('offline', None)}), (30, {'255': ('offline', 'offline')}), (60, {'255': ('offline', 'offline')})])
        self.assertEqual(events, [])

    def test_no_event_for_a_blip_shorter_than_the_confirmation(self):
        events, rows = self.run_cycles([(0, {'255': ('offline', 'online')}), (30, {'255': ('online', 'offline')})])
        self.assertEqual((events, rows), ([], []))

    def test_offline_event_comes_when_the_outage_is_confirmed_not_before(self):
        events, _ = self.run_cycles([(0, {'255': ('offline', 'online')})])
        self.assertEqual(events, [])
        events, _ = self.run_cycles([(0, {'255': ('offline', 'online')}), (30, {'255': ('offline', 'offline')}), (60, {'255': ('offline', 'offline')})])
        self.assertEqual(events, [('offline', '255', T0 + timedelta(seconds=30))])   # once, however long it lasts

    def test_online_event_when_the_phone_comes_back(self):
        events, rows = self.run_cycles([(0, {'255': ('offline', 'online')}), (30, {'255': ('offline', 'offline')}), (90, {'255': ('online', 'offline')})])
        self.assertEqual([e[0] for e in events], ['offline', 'online'])
        self.assertEqual(events[1][2] - rows[0].started_at, timedelta(seconds=90))

    def test_no_online_event_when_monitoring_just_stops(self):
        events, _ = self.run_cycles([(0, {'255': ('offline', 'online')}), (30, {'255': ('offline', 'offline')}), (60, {})])
        self.assertEqual([e[0] for e in events], ['offline'])

    def test_unknown_state_creates_no_event(self):
        events, _ = self.run_cycles([(0, {'255': ('unknown', 'online')}), (30, {'255': ('unknown', 'unknown')})])
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
