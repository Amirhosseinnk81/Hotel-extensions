"""Outage tracking through the real sync (database + Log.txt).

Run from the project folder:  python -m unittest tests.test_pbx_outages
Every test gets its own temporary database and its own logs/Log.txt.
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import app as m
import outage_monitor
from pbx_sync import AMIError
from tests.test_pbx_foundation import AppTestCase
from tests.test_pbx_sync import FakeClient, peer_event

T0 = datetime(2026, 10, 16, 10, 0, 0)  # UTC; sync_pbx_once(now=...) lets the tests move the clock


def on(ext):
    return peer_event(ext, '10.0.0.' + ext[-1], 'OK (3 ms)')


def off(ext):
    return peer_event(ext, '10.0.0.' + ext[-1], 'UNREACHABLE')


def local(minutes):
    return m.format_local_datetime(T0 + timedelta(minutes=minutes), m.OUTAGE_TIME_FORMAT)


class OutageCase(AppTestCase):
    ENV = {'PBX_SYNC_AUTOSTART': '0', 'PBX_OUTAGE_MONITOR': '1', 'PBX_OUTAGE_CONFIRM_SYNCS': '2',
           'PBX_OUTAGE_KINDS': 'staff,room', 'PBX_OUTAGE_LOG': ''}

    def setUp(self):
        patcher = mock.patch.dict(os.environ, self.ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        m.pbx_sync_state.update(running=False, last_attempt=None, last_success=None, last_error=None,
                                last_duration=None, summary=None, error_since=None)
        super().setUp()
        self.addCleanup(self.close_log)
        self.log_path = os.path.join(self.tmp, 'logs', 'Log.txt')
        self.add('255', 'Reception')
        self.add('256', 'Kitchen')

    def close_log(self):
        for handler in list(outage_monitor.outage_logger.handlers):
            outage_monitor.outage_logger.removeHandler(handler)
            handler.close()

    def sync(self, events, minutes):
        with self.app.app_context():
            return m.sync_pbx_once(lambda: FakeClient(events=events, agents={}), now=T0 + timedelta(minutes=minutes))

    def outages(self):
        with self.app.app_context():
            rows = m.PhoneOutage.query.order_by(m.PhoneOutage.id).all()
            return [{**{c.name: getattr(o, c.name) for c in m.PhoneOutage.__table__.columns}, 'duration': o.duration_seconds} for o in rows]

    def log_lines(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding='utf-8-sig') as f:
            return f.read().splitlines()

    def edit(self, extension, **fields):
        with self.app.app_context():
            entry = m.Extension.query.filter_by(extension=extension).first()
            for key, value in fields.items():
                setattr(entry, key, value)
            m.db.session.commit()


class OutageSyncTests(OutageCase):
    def test_outage_is_recorded_logged_and_closed(self):
        self.sync([on('255'), on('256')], 0)
        self.sync([off('255'), on('256')], 1)
        rows = self.outages()
        self.assertEqual((len(rows), rows[0]['confirmed']), (1, False))   # seen once: not confirmed, not logged
        self.assertEqual(self.log_lines(), [])
        summary = self.sync([off('255'), on('256')], 2)
        self.assertEqual(summary['went_offline'], 1)
        row = self.outages()[0]
        self.assertEqual((row['extension'], row['confirmed'], row['started_at'], row['ended_at']), ('255', True, T0 + timedelta(minutes=1), None))
        self.assertEqual(row['title'], 'Reception')
        self.assertEqual(row['last_ip'], '10.0.0.5')
        self.assertEqual(len(self.log_lines()), 1)
        self.assertIn(f"| OFFLINE  | 255 | 10.0.0.5 | down since {local(1)} | Reception", self.log_lines()[0])
        summary = self.sync([on('255'), on('256')], 14)
        self.assertEqual(summary['came_online'], 1)
        row = self.outages()[0]
        self.assertEqual((row['end_reason'], row['ended_at'], row['duration']), ('online', T0 + timedelta(minutes=14), 13 * 60))
        self.assertEqual(len(self.log_lines()), 2)
        self.assertIn('| ONLINE   | 255 | 10.0.0.5 | was down 00:13:00 | Reception', self.log_lines()[1])

    def test_phone_that_was_already_offline_is_not_an_outage(self):
        for minute in range(4):
            self.sync([off('255'), on('256')], minute)
        self.assertEqual((self.outages(), self.log_lines()), ([], []))

    def test_short_blip_leaves_no_trace(self):
        self.sync([on('255'), on('256')], 0)
        self.sync([off('255'), on('256')], 1)
        self.sync([on('255'), on('256')], 2)
        self.assertEqual((self.outages(), self.log_lines()), ([], []))

    def test_old_previous_state_is_not_trusted(self):
        self.sync([on('255'), on('256')], 0)
        self.sync([off('255'), on('256')], 30)   # 30 minutes without a sync: we do not know when it went down
        self.sync([off('255'), on('256')], 31)
        self.assertEqual((self.outages(), self.log_lines()), ([], []))

    def test_inactive_and_pending_phones_are_not_watched(self):
        self.edit('256', is_active=False)
        self.sync([on('255'), on('256'), on('777')], 0)   # 777 is new on the PBX, so it is created as 'pending'
        self.sync([on('255'), off('256'), off('777')], 1)
        self.sync([on('255'), off('256'), off('777')], 2)
        self.assertEqual((self.outages(), self.log_lines()), ([], []))

    def test_disabling_a_phone_in_the_middle_of_an_outage_stops_it(self):
        self.sync([on('255'), on('256')], 0)
        self.sync([off('255'), on('256')], 1)
        self.sync([off('255'), on('256')], 2)
        self.edit('255', is_active=False)
        self.sync([off('255'), on('256')], 5)
        row = self.outages()[0]
        self.assertEqual((row['end_reason'], row['ended_at']), ('stopped', T0 + timedelta(minutes=5)))
        self.assertIn('| STOPPED  | 255 |', self.log_lines()[-1])

    def test_trashing_a_phone_in_the_middle_of_an_outage_stops_it(self):
        self.sync([on('255'), on('256')], 0)
        self.sync([off('255'), on('256')], 1)
        self.sync([off('255'), on('256')], 2)
        self.edit('255', is_deleted=True)
        self.sync([off('255'), on('256')], 3)
        self.assertEqual(self.outages()[0]['end_reason'], 'stopped')

    def test_an_empty_answer_from_the_pbx_does_not_close_outages(self):
        self.sync([on('255'), on('256')], 0)
        self.sync([off('255'), on('256')], 1)
        self.sync([off('255'), on('256')], 2)
        self.sync([], 3)
        self.assertIsNone(self.outages()[0]['ended_at'])
        self.assertEqual(len(self.log_lines()), 1)

    def test_two_phones_are_tracked_independently(self):
        self.sync([on('255'), on('256')], 0)
        self.sync([off('255'), off('256')], 1)
        self.sync([off('255'), on('256')], 2)   # 256 recovered before being confirmed
        rows = self.outages()
        self.assertEqual([r['extension'] for r in rows], ['255'])
        self.assertTrue(rows[0]['confirmed'])

    def test_second_outage_is_a_new_record(self):
        self.sync([on('255')], 0)
        self.sync([off('255')], 1)
        self.sync([off('255')], 2)
        self.sync([on('255')], 5)
        self.sync([off('255')], 6)
        self.sync([off('255')], 7)
        rows = self.outages()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]['ended_at'] is None, rows[1]['ended_at'] is None), (False, True))

    def test_confirm_after_one_sync_when_configured(self):
        with mock.patch.dict(os.environ, {'PBX_OUTAGE_CONFIRM_SYNCS': '1'}):
            self.sync([on('255')], 0)
            self.sync([off('255')], 1)
        self.assertEqual(len(self.log_lines()), 1)

    def test_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {'PBX_OUTAGE_MONITOR': '0'}):
            self.sync([on('255')], 0)
            self.sync([off('255')], 1)
            summary = self.sync([off('255')], 2)
        self.assertEqual((self.outages(), self.log_lines(), summary['went_offline']), ([], [], 0))

    def test_only_the_chosen_kinds_are_watched(self):
        with mock.patch.dict(os.environ, {'PBX_OUTAGE_KINDS': 'room'}):
            self.sync([on('255')], 0)
            self.sync([off('255')], 1)
            self.sync([off('255')], 2)
        self.assertEqual(self.outages(), [])

    def test_persian_title_survives_in_the_log_and_the_table(self):
        self.add('300', 'پذیرش مرکزی')
        self.sync([on('300')], 0)
        self.sync([off('300')], 1)
        self.sync([off('300')], 2)
        self.assertTrue(self.log_lines()[0].endswith('| پذیرش مرکزی'))
        self.assertEqual(self.outages()[0]['title'], 'پذیرش مرکزی')

    def test_status_columns_are_still_updated(self):
        self.sync([on('255')], 0)
        self.sync([off('255')], 1)
        self.assertEqual(self.get_ext('255')['reg_status'], 'offline')

    def test_log_is_written_after_the_commit(self):
        self.sync([on('255')], 0)
        self.sync([off('255')], 1)
        with mock.patch.object(m.db.session, 'commit', side_effect=RuntimeError('disk full')):
            with self.app.app_context():
                with self.assertRaises(RuntimeError):
                    m.sync_pbx_once(lambda: FakeClient(events=[off('255')], agents={}), now=T0 + timedelta(minutes=2))
        self.assertEqual(self.log_lines(), [])


class PbxLinkLogTests(OutageCase):
    def test_pbx_loss_and_return_are_logged_once_each(self):
        def broken():
            raise AMIError('cannot connect to 192.168.228.2:5038 (TimeoutError)')

        m.run_pbx_sync(self.app, broken)
        m.run_pbx_sync(self.app, broken)   # still down: no second line
        self.assertEqual(len(self.log_lines()), 1)
        self.assertIn('| PBX-LOST | - | - | cannot read the PBX: cannot connect', self.log_lines()[0])
        ok, _ = m.run_pbx_sync(self.app, lambda: FakeClient(events=[on('255')], agents={}))
        self.assertTrue(ok)
        self.assertEqual(len(self.log_lines()), 2)
        self.assertIn('| PBX-OK   | - | - | reachable again after', self.log_lines()[1])
        m.run_pbx_sync(self.app, lambda: FakeClient(events=[on('255')], agents={}))
        self.assertEqual(len(self.log_lines()), 2)

    def test_a_pbx_failure_creates_no_phone_outages(self):
        self.sync([on('255'), on('256')], 0)

        def broken():
            raise AMIError('down')

        m.run_pbx_sync(self.app, broken)
        self.assertEqual(self.outages(), [])


if __name__ == '__main__':
    unittest.main()
