"""Outage events, the alert feed, the report page, the CSV export and the purge, through the real app.

Run from the project folder:  python -m unittest tests.test_pbx_outage_alerts
Every test gets its own temporary database and its own logs/Log.txt (see OutageCase).
"""
import csv
import io
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import app as m
from pbx_sync import AMIError
from tests.test_pbx_outages import OutageCase, T0, off, on
from tests.test_pbx_sync import FakeClient


def utc_now_naive():
    return datetime.utcnow()


class AlertCase(OutageCase):
    ENV = {**OutageCase.ENV, 'PBX_ALERTS_ENABLED': '1', 'PBX_ALERT_SUMMARY_THRESHOLD': '5', 'PBX_ALERT_ON_RECOVERY': '1'}

    def events(self):
        with self.app.app_context():
            rows = m.OutageEvent.query.order_by(m.OutageEvent.id).all()
            return [{c.name: getattr(e, c.name) for c in m.OutageEvent.__table__.columns} for e in rows]

    def feed(self, after=None, client=None):
        query = '' if after is None else f'?after={after}'
        return (client or self.admin).get('/api/outages/alerts' + query)

    def add_outage(self, extension, title='Desk', started=None, ended=None, reason=None, confirmed=True, ip='10.0.0.9'):
        with self.app.app_context():
            m.db.session.add(m.PhoneOutage(extension=extension, title=title, last_ip=ip, started_at=started or T0, ended_at=ended,
                                           end_reason=reason, confirmed=confirmed, offline_syncs=2))
            m.db.session.commit()

    def count_outages(self):
        with self.app.app_context():
            return m.PhoneOutage.query.count()


class EventCreationTests(AlertCase):
    def test_nothing_happens_on_the_first_sync(self):
        self.sync([off('255'), on('256')], 0)
        self.assertEqual(self.events(), [])
        self.sync([off('255'), on('256')], 1)
        self.assertEqual(self.events(), [])    # it was never seen online: not an outage

    def test_a_short_blip_leaves_no_event(self):
        self.sync([on('255')], 0)
        self.sync([off('255')], 1)
        self.sync([on('255')], 2)
        self.assertEqual(self.events(), [])

    def test_offline_event_after_confirmation_and_online_event_when_back(self):
        self.sync([on('255')], 0)
        self.sync([off('255')], 1)
        self.assertEqual(self.events(), [])    # debounce: one sync is not enough
        self.sync([off('255')], 2)
        self.sync([off('255')], 3)
        events = self.events()
        self.assertEqual([(e['kind'], e['extension'], e['title']) for e in events], [('offline', '255', 'Reception')])
        self.assertEqual(events[0]['detail'], m.format_local_datetime(T0 + timedelta(minutes=1), '%H:%M'))
        self.assertEqual(events[0]['batch'], T0 + timedelta(minutes=2))
        self.sync([on('255')], 14)
        events = self.events()
        self.assertEqual([e['kind'] for e in events], ['offline', 'online'])
        self.assertEqual(events[1]['detail'], '00:13:00')    # same duration as the log line and the report

    def test_a_pbx_failure_creates_no_events(self):
        self.sync([on('255')], 0)

        def broken():
            raise AMIError('down')

        m.run_pbx_sync(self.app, broken)
        m.run_pbx_sync(self.app, broken)
        self.assertEqual(self.events(), [])

    def test_alerts_switched_off_creates_no_events(self):
        with mock.patch.dict(os.environ, {'PBX_ALERTS_ENABLED': '0'}):
            self.sync([on('255')], 0)
            self.sync([off('255')], 1)
            self.sync([off('255')], 2)
            self.assertEqual(len(self.outages()), 1)    # the outage itself is still recorded
            self.assertEqual(self.events(), [])


class AlertFeedTests(AlertCase):
    def go_offline(self, extensions, minute=1):
        peers_on = [on(e) for e in extensions]
        self.sync(peers_on, minute)
        self.sync([off(e) for e in extensions], minute + 1)
        self.sync([off(e) for e in extensions], minute + 2)

    def test_anonymous_users_get_401(self):
        response = self.feed(client=self.anon)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_first_call_only_returns_the_baseline(self):
        self.go_offline(['255'])
        data = self.feed().get_json()
        self.assertEqual((data['ok'], data['enabled'], data['alerts']), (True, True, []))
        self.assertEqual(data['latest_id'], 1)

    def test_new_events_come_as_alerts(self):
        self.go_offline(['255'])
        data = self.feed(after=0).get_json()
        self.assertEqual(len(data['alerts']), 1)
        alert = data['alerts'][0]
        self.assertEqual(alert['title'], '☎ داخلی 255 (Reception) آفلاین شد')
        self.assertTrue(alert['body'].startswith('قطع از ساعت '))
        self.assertFalse(alert['quiet'])
        self.assertTrue(alert['url'].endswith('/outages'))
        self.assertEqual(self.feed(after=data['latest_id']).get_json()['alerts'], [])    # nothing is delivered twice

    def test_recovery_alert_is_quiet(self):
        self.go_offline(['255'])
        last = self.feed().get_json()['latest_id']
        self.sync([on('255')], 20)
        [alert] = self.feed(after=last).get_json()['alerts']
        self.assertEqual(alert['kind'], 'online')
        self.assertTrue(alert['quiet'])
        self.assertIn('دوباره وصل شد', alert['title'])

    def test_recovery_alerts_can_be_switched_off(self):
        self.go_offline(['255'])
        last = self.feed().get_json()['latest_id']
        with mock.patch.dict(os.environ, {'PBX_ALERT_ON_RECOVERY': '0'}):
            self.sync([on('255')], 20)
            self.assertEqual(self.feed(after=last).get_json()['alerts'], [])

    def test_many_phones_at_once_become_one_summary(self):
        extensions = [str(300 + i) for i in range(7)]
        for e in extensions:
            self.add(e, 'Room ' + e)
        self.go_offline(extensions)
        alerts = self.feed(after=0).get_json()['alerts']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['title'], '☎ ۷ تلفن همزمان آفلاین شدند')

    def test_the_threshold_is_configurable(self):
        extensions = [str(300 + i) for i in range(3)]
        for e in extensions:
            self.add(e, 'Room ' + e)
        with mock.patch.dict(os.environ, {'PBX_ALERT_SUMMARY_THRESHOLD': '2'}):
            self.go_offline(extensions)
            alerts = self.feed(after=0).get_json()['alerts']
            self.assertEqual([a['kind'] for a in alerts], ['summary-offline'])

    def test_exactly_the_threshold_stays_individual(self):
        extensions = [str(300 + i) for i in range(5)]
        for e in extensions:
            self.add(e, 'Room ' + e)
        self.go_offline(extensions)
        self.assertEqual(len(self.feed(after=0).get_json()['alerts']), 5)

    def test_old_events_are_not_replayed(self):
        self.go_offline(['255'])
        with self.app.app_context():
            for event in m.OutageEvent.query.all():
                event.created_at = datetime.utcnow() - timedelta(hours=2)
            m.db.session.commit()
        data = self.feed(after=0).get_json()
        self.assertEqual(data['alerts'], [])
        self.assertEqual(data['latest_id'], 1)

    def test_a_reset_database_gives_a_new_baseline(self):
        self.go_offline(['255'])
        data = self.feed(after=999).get_json()    # the browser remembers an id the database no longer has
        self.assertEqual((data['alerts'], data['latest_id']), ([], 1))

    def test_disabled_feed_is_empty(self):
        self.go_offline(['255'])
        with mock.patch.dict(os.environ, {'PBX_ALERTS_ENABLED': '0'}):
            data = self.feed(after=0).get_json()
            self.assertEqual((data['enabled'], data['alerts']), (False, []))


class BellTests(AlertCase):
    def test_bell_is_only_for_signed_in_admins_when_alerts_are_on(self):
        self.assertIn('id="alertToggle"', self.admin.get(self.url('')).get_data(as_text=True))
        self.assertIn('data-alerts-url="/api/outages/alerts"', self.admin.get(self.url('')).get_data(as_text=True))
        self.assertNotIn('alertToggle', self.anon.get('/').get_data(as_text=True))
        with mock.patch.dict(os.environ, {'PBX_ALERTS_ENABLED': '0'}):
            self.assertNotIn('alertToggle', self.admin.get(self.url('')).get_data(as_text=True))

    def test_menu_links_to_the_report(self):
        self.assertIn(self.url('/outages'), self.admin.get(self.url('')).get_data(as_text=True))


class ReportPageTests(AlertCase):
    def setUp(self):
        super().setUp()
        # relative to the real clock: the page's statistics look at the last 30 days
        self.base = datetime.utcnow().replace(microsecond=0) - timedelta(days=3)
        self.add_outage('255', 'Reception', started=self.base, ended=self.base + timedelta(minutes=13), reason='online')
        self.add_outage('256', 'Kitchen', started=self.base + timedelta(days=1), ended=None)
        self.add_outage('257', 'Bar', started=self.base + timedelta(days=2), ended=self.base + timedelta(days=2, minutes=5), reason='stopped')
        self.add_outage('258', 'Blip', started=self.base + timedelta(hours=1), confirmed=False)    # never confirmed: never shown

    def page(self, query=''):
        return self.admin.get(self.url('/outages') + query).get_data(as_text=True)

    def test_page_needs_a_login(self):
        response = self.anon.get(self.url('/outages'))
        self.assertEqual(response.status_code, 302)

    def test_rows_columns_and_ongoing_highlight(self):
        html = self.page()
        for text in ('Reception', 'Kitchen', 'Bar', '00:13:00', 'هنوز قطع است', 'پایان پایش', 'گزارش قطعی تلفن‌ها'):
            self.assertIn(text, html)
        self.assertNotIn('Blip', html)
        self.assertEqual(html.count('class="is-ongoing"'), 1)    # only the outage that is still going on
        self.assertIn('data-since=', html)    # the live duration ticks from this

    def test_only_ongoing(self):
        html = self.page('?ongoing=1')
        self.assertIn('Kitchen', html)
        self.assertNotIn('Reception', html)

    def test_search_by_extension_with_persian_digits_title_and_ip(self):
        self.assertIn('Reception', self.page('?q=۲۵۵'))
        self.assertNotIn('Kitchen', self.page('?q=۲۵۵'))
        self.assertIn('Kitchen', self.page('?q=kitch'))
        self.assertIn('Kitchen', self.page('?q=10.0.0.9'))

    def test_date_range_uses_the_start_time_in_local_days(self):
        day = m.format_local_datetime(self.base, '%Y-%m-%d')
        html = self.page(f'?from={day}&to={day}')
        self.assertIn('Reception', html)
        self.assertNotIn('Kitchen', html)
        nobody = m.format_local_datetime(self.base - timedelta(days=30), '%Y-%m-%d')
        self.assertNotIn('Reception', self.page(f'?from={nobody}&to={nobody}'))

    def test_garbage_filters_are_ignored(self):
        self.assertIn('Reception', self.page('?from=nonsense&to=2026-99-99&ongoing=maybe'))

    def test_stats(self):
        html = self.page()
        self.assertIn('outage-stat is-hot', html)    # one outage is going on right now
        worst = html.split('<ol class="outage-worst">')[1].split('</ol>')[0]
        for name in ('Reception', 'Kitchen', 'Bar'):
            self.assertIn(name, worst)
        self.assertNotIn('Blip', worst)

    def test_empty_report(self):
        with self.app.app_context():
            m.PhoneOutage.query.delete()
            m.db.session.commit()
        html = self.page()
        self.assertIn('قطعی‌ای با این فیلترها ثبت نشده است', html)
        self.assertNotIn('outage-stat is-hot', html)

    def test_page_limit_is_announced(self):
        with mock.patch.object(m, 'OUTAGE_PAGE_LIMIT', 2):
            html = self.page()
            self.assertIn('فقط 2 مورد آخر نمایش داده می‌شود', html)


class CsvExportTests(AlertCase):
    def setUp(self):
        super().setUp()
        self.add_outage('255', 'Reception', started=T0, ended=T0 + timedelta(minutes=13), reason='online')
        self.add_outage('256', '=HYPERLINK("http://x")', started=T0 + timedelta(days=1), ended=None)

    def rows(self, query=''):
        response = self.admin.get(self.url('/outages/export.csv') + query)
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response.headers['Content-Type'])
        self.assertIn('attachment', response.headers['Content-Disposition'])
        raw = response.get_data()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))    # BOM: Excel opens the Persian text correctly
        return list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))

    def test_needs_a_login(self):
        self.assertEqual(self.anon.get(self.url('/outages/export.csv')).status_code, 302)

    def test_columns_and_values(self):
        rows = self.rows()
        self.assertEqual(rows[0], ['extension', 'title', 'ip', 'started_at', 'ended_at', 'duration_seconds', 'duration', 'status'])
        by_ext = {r[0]: r for r in rows[1:]}
        self.assertEqual(by_ext['255'][5:], ['780', '00:13:00', 'ended'])
        self.assertEqual(by_ext['255'][3], m.format_local_datetime(T0, '%Y-%m-%d %H:%M:%S'))
        self.assertEqual(by_ext['256'][4], '')    # still going on: no end time
        self.assertEqual(by_ext['256'][7], 'ongoing')

    def test_formula_in_a_title_is_defused(self):
        by_ext = {r[0]: r for r in self.rows()[1:]}
        self.assertTrue(by_ext['256'][1].startswith("'="))

    def test_export_follows_the_filters(self):
        rows = self.rows('?ongoing=1')
        self.assertEqual([r[0] for r in rows[1:]], ['256'])


class PurgeTests(AlertCase):
    NOW = datetime(2026, 10, 16, 12, 0, 0)

    def days(self, n):
        return self.NOW - timedelta(days=n)

    def purge(self, keep_days=180, force=True):
        with self.app.app_context():
            return m.purge_old_outage_data(self.NOW, keep_days, force=force)

    def setUp(self):
        super().setUp()
        m._outage_purge_state['at'] = None
        self.add_outage('255', 'Old', started=self.days(201), ended=self.days(200), reason='online')
        self.add_outage('256', 'Recent', started=self.days(11), ended=self.days(10), reason='online')
        self.add_outage('257', 'Still down', started=self.days(300), ended=None)

    def remaining(self):
        with self.app.app_context():
            return sorted(o.extension for o in m.PhoneOutage.query.all())

    def test_only_old_finished_outages_are_deleted(self):
        self.assertEqual(self.purge(180), 1)
        self.assertEqual(self.remaining(), ['256', '257'])    # an outage that is still going on is never purged

    def test_zero_keeps_everything(self):
        self.assertEqual(self.purge(0), 0)
        self.assertEqual(len(self.remaining()), 3)

    def test_old_alert_events_are_deleted_too(self):
        with self.app.app_context():
            m.db.session.add(m.OutageEvent(kind='offline', batch=self.days(5), extension='255', created_at=self.days(5)))
            m.db.session.add(m.OutageEvent(kind='offline', batch=self.days(1), extension='256', created_at=self.days(1)))
            m.db.session.commit()
        self.purge(180)
        self.assertEqual([e['extension'] for e in self.events()], ['256'])

    def test_it_does_not_run_on_every_sync(self):
        self.assertEqual(self.purge(180, force=False), 1)
        self.add_outage('258', 'Also old', started=self.days(401), ended=self.days(400), reason='online')
        self.assertEqual(self.purge(180, force=False), 0)    # throttled: ran moments ago
        self.assertEqual(self.purge(180, force=True), 1)

    def test_a_sync_runs_the_purge(self):
        with mock.patch.dict(os.environ, {'PBX_OUTAGE_KEEP_DAYS': '180'}):
            with self.app.app_context():
                m.sync_pbx_once(lambda: FakeClient(events=[on('255')], agents={}), now=self.NOW)
            self.assertEqual(self.remaining(), ['256', '257'])

    def test_a_failing_purge_does_not_fail_the_sync(self):
        with mock.patch.object(m, 'purge_old_outage_data', side_effect=RuntimeError('locked')):
            with self.app.app_context():
                summary = m.sync_pbx_once(lambda: FakeClient(events=[on('255')], agents={}), now=self.NOW)
            self.assertEqual(summary['online'], 1)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        keys = ('PBX_ALERTS_ENABLED', 'PBX_ALERT_SUMMARY_THRESHOLD', 'PBX_ALERT_ON_RECOVERY', 'PBX_OUTAGE_KEEP_DAYS', 'PBX_OUTAGE_MONITOR')
        base = {'PBX_WEB_HOST': 'https://10.0.0.1', 'PBX_WEB_USER': 'u', 'PBX_WEB_PASS': 'p'}
        with mock.patch.dict(os.environ, base):
            for key in keys:
                os.environ.pop(key, None)
            cfg = m.pbx_config()
            self.assertEqual((cfg['alerts_enabled'], cfg['alert_summary_threshold'], cfg['alert_on_recovery'], cfg['outage_keep_days']),
                             (True, 5, True, 180))
            os.environ.update({'PBX_ALERTS_ENABLED': '0', 'PBX_ALERT_SUMMARY_THRESHOLD': '9', 'PBX_ALERT_ON_RECOVERY': '0', 'PBX_OUTAGE_KEEP_DAYS': '30'})
            cfg = m.pbx_config()
            self.assertEqual((cfg['alerts_enabled'], cfg['alert_summary_threshold'], cfg['alert_on_recovery'], cfg['outage_keep_days']),
                             (False, 9, False, 30))

    def test_no_alerts_when_the_outage_tracking_is_off(self):
        with mock.patch.dict(os.environ, {'PBX_WEB_HOST': 'https://10.0.0.1', 'PBX_WEB_USER': 'u', 'PBX_WEB_PASS': 'p',
                                           'PBX_OUTAGE_MONITOR': '0', 'PBX_ALERTS_ENABLED': '1'}):
            self.assertFalse(m.pbx_config()['alerts_enabled'])

    def test_no_alerts_without_a_pbx_link(self):
        with mock.patch.dict(os.environ, {'PBX_ALERTS_ENABLED': '1'}):
            for key in ('PBX_AMI_HOST', 'PBX_AMI_USER', 'PBX_AMI_SECRET', 'PBX_WEB_HOST', 'PBX_WEB_USER', 'PBX_WEB_PASS'):
                os.environ.pop(key, None)
            self.assertFalse(m.pbx_config()['alerts_enabled'])


if __name__ == '__main__':
    unittest.main()
