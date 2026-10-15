"""Tests for the Issabel web-panel source inside the app: configuration, the database sync, what
happens when the panel is unreachable, and the JSON API / badges that show the result.

The scraper itself (login, session, parsing) is tested in test_pbx_web_scraper.py; here it is
replaced by a fake so only the app side is exercised.
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import app as m
from pbx_sync import PBXWebError, PBXWebLoginRejected, parse_peer_listing
from tests.test_pbx_sync import SyncCase
from tests.test_pbx_web_scraper import SIP_SHOW_PEERS

NO_PBX = {name: '' for name in ('PBX_AMI_HOST', 'PBX_AMI_USER', 'PBX_AMI_SECRET', 'PBX_WEB_HOST', 'PBX_WEB_USER', 'PBX_WEB_PASS',
                                'PBX_SOURCE', 'PBX_SYNC_INTERVAL', 'PBX_SYNC_INTERVAL_SECONDS', 'PBX_VERIFY_SSL',
                                'PBX_WEB_TIMEOUT', 'PBX_WEB_LOGIN_PATH', 'PBX_WEB_PEERS_PATH')}
WEB_ENV = {**NO_PBX, 'PBX_WEB_HOST': 'https://192.168.1.100', 'PBX_WEB_USER': 'monitor', 'PBX_WEB_PASS': 'web-secret-pass'}


class FakeScraper:
    def __init__(self, text=SIP_SHOW_PEERS):
        self.peers, self.calls = parse_peer_listing(text), 0

    def fetch_peer_states(self):
        self.calls += 1
        return dict(self.peers)


class BrokenScraper:
    def __init__(self, error):
        self.error = error

    def fetch_peer_states(self):
        raise self.error


class WebCase(SyncCase):
    def use_env(self, values=WEB_ENV):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, scraper=None):
        with self.app.app_context():
            return m.sync_pbx_once(web_scraper=scraper or FakeScraper())


class ConfigTests(unittest.TestCase):
    def cfg(self, **values):
        with mock.patch.dict(os.environ, {**NO_PBX, **values}):
            return m.pbx_config()

    def test_off_by_default(self):
        cfg = self.cfg()
        self.assertEqual((cfg['enabled'], cfg['source']), (False, None))

    def test_web_only(self):
        cfg = self.cfg(**{k: v for k, v in WEB_ENV.items() if k.startswith('PBX_WEB_')})
        self.assertEqual((cfg['enabled'], cfg['source']), (True, 'web'))
        self.assertEqual((cfg['interval'], cfg['web_verify_ssl']), (30, False))  # documented defaults
        self.assertEqual((cfg['web_login_path'], cfg['web_peers_path']), ('/index.php', '/index.php?menu=asterisk_info&rawmode=yes'))

    def test_an_incomplete_web_setting_is_off(self):
        self.assertFalse(self.cfg(PBX_WEB_HOST='https://x', PBX_WEB_USER='u')['enabled'])

    def test_ami_wins_in_auto_mode_and_the_source_can_be_forced(self):
        both = dict(WEB_ENV, PBX_AMI_HOST='10.0.0.1', PBX_AMI_USER='u', PBX_AMI_SECRET='s')
        self.assertEqual(self.cfg(**both)['source'], 'ami')
        self.assertEqual(self.cfg(**dict(both, PBX_SOURCE='web'))['source'], 'web')
        self.assertIsNone(self.cfg(PBX_SOURCE='web', PBX_AMI_HOST='10.0.0.1', PBX_AMI_USER='u', PBX_AMI_SECRET='s')['source'])

    def test_interval_and_ssl_settings(self):
        self.assertEqual(self.cfg(PBX_SYNC_INTERVAL='60')['interval'], 60)
        self.assertEqual(self.cfg(PBX_SYNC_INTERVAL='5')['interval'], 15)           # lower bound
        self.assertEqual(self.cfg(PBX_SYNC_INTERVAL_SECONDS='90')['interval'], 90)  # the older name still works
        self.assertEqual(self.cfg(PBX_SYNC_INTERVAL='junk')['interval'], 30)
        self.assertTrue(self.cfg(PBX_VERIFY_SSL='True')['web_verify_ssl'])
        self.assertFalse(self.cfg(PBX_VERIFY_SSL='0')['web_verify_ssl'])


class WebSyncTests(WebCase):
    def test_web_peers_update_the_database(self):
        summary = self.sync()
        e100, e101 = self.get_ext('100'), self.get_ext('101')
        self.assertEqual((e100['reg_status'], e100['rtt_ms']), ('online', 3))
        self.assertEqual((e101['reg_status'], e101['rtt_ms'], e101['last_ip']), ('online', 5, '192.168.231.247'))
        self.assertIsNotNone(e101['status_updated_at'])
        self.assertIsNotNone(e101['last_seen_at'])
        self.assertEqual(summary['ignored'], 1)  # the trunk
        self.assertEqual(summary['created'], 5)  # 1003, 1004, 1005, 2001 are rooms, 777 is pending (1006 is trashed)
        self.assertEqual(self.get_ext('777')['kind'], 'pending')
        self.assertEqual(self.get_ext('1003')['kind'], 'room')

    def test_offline_and_not_registered_states(self):
        self.sync()
        self.assertEqual(self.get_ext('1005')['reg_status'], 'offline')   # UNREACHABLE
        self.assertEqual(self.get_ext('1004')['reg_status'], 'offline')   # (Unspecified): not registered
        self.assertIsNone(self.get_ext('1005')['rtt_ms'])
        self.assertEqual(self.get_ext('2001')['reg_status'], 'online')    # Unmonitored with an address

    def test_the_web_source_never_touches_model_mac_or_user_agent(self):
        self.add('102', 'Has Data', phone_model='Yealink T31P', mac='00:15:65:AA:BB:CC', user_agent='Yealink SIP-T31P 1.2.3.4')
        self.sync(FakeScraper(SIP_SHOW_PEERS + '102/102        192.168.1.5     D  Yes  No   5060  OK (4 ms)\n'))
        row = self.get_ext('102')
        self.assertEqual((row['phone_model'], row['mac'], row['user_agent']), ('Yealink T31P', '00:15:65:AA:BB:CC', 'Yealink SIP-T31P 1.2.3.4'))
        self.assertEqual(row['reg_status'], 'online')
        self.assertIsNone(self.get_ext('100')['user_agent'])

    def test_extensions_missing_from_the_page_lose_their_live_state(self):
        self.sync()
        row = self.get_ext('150')
        self.assertEqual((row['reg_status'], row['status_updated_at']), (None, None))

    def test_the_source_is_chosen_from_the_environment(self):
        self.use_env()
        scraper = FakeScraper()
        with mock.patch.object(m, 'shared_scraper', return_value=scraper) as factory, self.app.app_context():
            m.sync_pbx_once()
        factory.assert_called_once_with('https://192.168.1.100', 'monitor', 'web-secret-pass', False, 10.0, '/index.php',
                                        '/index.php?menu=asterisk_info&rawmode=yes')
        self.assertEqual(scraper.calls, 1)

    def test_runner_and_thread_work_with_the_web_source(self):
        self.use_env()
        with mock.patch.object(m, 'shared_scraper', return_value=FakeScraper()):
            ok, summary = m.run_pbx_sync(self.app)
        self.assertTrue(ok)
        self.assertEqual(summary['online'], m.pbx_sync_state['summary']['online'])
        with mock.patch.dict(os.environ, {**WEB_ENV, 'PBX_WEB_PASS': ''}):
            self.assertIsNone(m.start_pbx_sync_thread(self.app))


class WebFailureTests(WebCase):
    def test_login_failure_is_logged_and_the_old_state_is_kept(self):
        self.use_env()
        before = self.get_ext('101')
        broken = BrokenScraper(PBXWebLoginRejected('Issabel rejected the web user name / password'))
        with mock.patch.object(m, 'shared_scraper', return_value=broken), self.assertLogs(self.app.logger, level='WARNING') as logs:
            ok, message = m.run_pbx_sync(self.app)
        self.assertFalse(ok)
        self.assertIn('rejected', message)
        self.assertTrue(any('PBX sync failed' in line for line in logs.output))
        self.assertNotIn('web-secret-pass', ' '.join(logs.output) + message)
        after = self.get_ext('101')
        self.assertEqual((after['reg_status'], after['status_updated_at']), (before['reg_status'], before['status_updated_at']))
        self.assertIsNone(m.pbx_sync_state['last_success'])

    def test_lost_connection_does_not_crash_and_the_state_goes_stale_to_unknown(self):
        self.use_env()
        with mock.patch.object(m, 'shared_scraper', return_value=FakeScraper()):
            self.assertTrue(m.run_pbx_sync(self.app)[0])
        with self.app.app_context():  # pretend the last good answer is older than the stale limit
            entry = m.Extension.query.filter_by(extension='101').first()
            entry.status_updated_at = datetime.utcnow() - timedelta(minutes=m.PBX_STATUS_STALE_MINUTES + 1)
            m.db.session.commit()
        down = BrokenScraper(PBXWebError('cannot reach the Issabel web panel https://192.168.1.100 (ConnectTimeout)'))
        with mock.patch.object(m, 'shared_scraper', return_value=down), self.assertLogs(self.app.logger, level='WARNING'):
            ok, message = m.run_pbx_sync(self.app)
        self.assertFalse(ok)
        self.assertIn('cannot reach', m.pbx_sync_state['last_error'])
        self.assertIsNotNone(m.pbx_sync_state['last_success'])  # the time of the last good check is kept
        with self.app.app_context():
            entry = m.Extension.query.filter_by(extension='101').first()
            self.assertEqual(m.display_phone_status(entry), 'unknown')
        with mock.patch.object(m, 'shared_scraper', return_value=FakeScraper()), self.assertLogs(self.app.logger, level='INFO') as logs:
            self.assertTrue(m.run_pbx_sync(self.app)[0])
        self.assertTrue(any('recovered' in line for line in logs.output))
        self.assertIsNone(m.pbx_sync_state['last_error'])


class StatusApiTests(WebCase):
    def setUp(self):
        super().setUp()
        self.use_env()
        self.sync(FakeScraper(SIP_SHOW_PEERS.replace('OK (5 ms)', 'LAGGED (210 ms)', 1)))  # 100 fast, 101 slow

    def test_public_gets_staff_only_without_network_details(self):
        response = self.anon.get('/api/extensions/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = response.get_json()
        self.assertEqual(sorted(data['statuses']), ['100', '101'])  # rooms / pending are hidden
        self.assertEqual(data['statuses']['100'], {'status': 'online', 'label': m.PHONE_STATUS_LABELS['online'], 'slow': False})
        self.assertTrue(data['statuses']['101']['slow'])
        self.assertEqual(data['statuses']['101']['label'], m.PHONE_SLOW_LABEL)
        text = response.get_data(as_text=True)
        for secret in ('192.168.231.247', '192.168.224.77', 'latency_ms', 'web-secret-pass', 'monitor'):
            self.assertNotIn(secret, text)
        self.assertNotIn('sync', data)
        self.assertEqual(data['interval'], 30)

    def test_admin_also_gets_rooms_latency_ip_and_sync_info(self):
        data = self.admin.get('/api/extensions/status').get_json()
        room = data['statuses']['1003']
        self.assertEqual((room['kind'], room['latency_ms'], room['ip'], room['status']), ('room', 210, '192.168.224.77', 'online'))
        self.assertEqual(data['statuses']['1005']['status'], 'offline')
        self.assertEqual(data['sync']['source'], 'web')
        self.assertIsNone(data['sync']['last_error'])

    def test_trashed_and_never_synced_extensions_are_left_out(self):
        data = self.admin.get('/api/extensions/status').get_json()
        self.assertNotIn('1006', data['statuses'])  # in the trash
        self.assertNotIn('184', data['statuses'])   # not on the PBX page

    def test_stale_data_is_reported_as_unknown(self):
        with self.app.app_context():
            for entry in m.Extension.query.all():
                if entry.status_updated_at:
                    entry.status_updated_at = datetime.utcnow() - timedelta(minutes=m.PBX_STATUS_STALE_MINUTES + 5)
            m.db.session.commit()
        data = self.anon.get('/api/extensions/status').get_json()
        self.assertEqual(data['statuses']['100']['status'], 'unknown')

    def test_public_page_has_live_badges_and_the_poller_hook(self):
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('data-status-url="/api/extensions/status"', html)
        self.assertIn('data-phone-pill', html)
        self.assertIn('phone-pill online slow', html)      # 101
        self.assertIn(m.PHONE_SLOW_LABEL, html)
        for secret in ('192.168.231.247', '192.168.224.77'):
            self.assertNotIn(secret, html)

    def test_no_poller_when_the_pbx_link_is_off(self):
        with mock.patch.dict(os.environ, NO_PBX):
            html = self.anon.get('/').get_data(as_text=True)
        self.assertNotIn('data-status-url', html)


class SyncNowApiTests(WebCase):
    def test_requires_a_signed_in_admin(self):
        self.use_env()
        response = self.anon.post('/api/pbx/sync-now')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['ok'], False)
        self.assertEqual(self.anon.get('/api/pbx/sync-now').status_code, 405)

    def test_runs_a_sync_and_returns_the_summary(self):
        self.use_env()
        with mock.patch.object(m, 'shared_scraper', return_value=FakeScraper()):
            response = self.admin.post('/api/pbx/sync-now')
        data = response.get_json()
        self.assertEqual((response.status_code, data['ok']), (200, True))
        self.assertEqual((data['summary']['online'], data['summary']['offline']), (5, 2))
        self.assertIsNotNone(data['last_sync'])

    def test_not_configured(self):
        with mock.patch.dict(os.environ, NO_PBX):
            response = self.admin.post('/api/pbx/sync-now')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()['ok'])

    def test_failure_is_a_json_error(self):
        self.use_env()
        broken = BrokenScraper(PBXWebError('cannot reach the Issabel web panel'))
        with mock.patch.object(m, 'shared_scraper', return_value=broken), self.assertLogs(self.app.logger, level='WARNING'):
            response = self.admin.post('/api/pbx/sync-now')
        self.assertEqual(response.status_code, 502)
        self.assertIn('cannot reach', response.get_json()['error'])

    def test_overlapping_run_is_refused(self):
        self.use_env()
        with m.pbx_sync_lock:
            response = self.admin.post('/api/pbx/sync-now')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()['ok'])


if __name__ == '__main__':
    unittest.main()
