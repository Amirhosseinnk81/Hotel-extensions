"""Issabel server health (pbx_health.py, read_health / apply_health_alerts, the page and the bell feed).

AMI is the fake server of test_pbx_connectors; the web dashboard is a fake scraper returning HTML.
The real Issabel 4 dashboard layout has NOT been seen: the sample below is an assumption, which is
why the parser only looks for loose "label ... NN%" patterns and tools/check_pbx.py --dashboard exists.
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import app as m
import features
import outage_alerts
import pbx_health
import pbx_snapshot
from pbx_connectors import ConnectorError
from pbx_health import AlertState, Thresholds
from tests.test_pbx_connectors import SECRET, AstServer
from tests.test_rbac import LEGACY_PASSWORD, AppCase

DASHBOARD = """<html><body><div id="applets">
<table><tr><td>CPU usage</td><td>12.5%</td></tr><tr><td>Memory usage</td><td>63 %</td></tr><tr><td>Swap usage</td><td>0%</td></tr></table>
<h3>Hard Drives</h3><table><tr><td>/</td><td>45%</td></tr><tr><td>/var/spool/asterisk</td><td>91%</td></tr><tr><td>/boot</td><td>30%</td></tr></table>
<h3>Processes</h3><table><tr><td>Asterisk</td><td>Running</td></tr><tr><td>MySQL</td><td>Stopped</td></tr><tr><td>Apache</td><td>Running</td></tr></table>
<a href="/index.php?menu=x">50%</a> <span>1/4 cores 50%</span>
</div></body></html>"""
TRUNKS = [{'Host': 'sip.provider.example', 'Port': '5060', 'Username': 'trunk1', 'State': 'Registered'},
          {'Host': '10.1.1.1', 'Port': '5060', 'Username': 'trunk2', 'State': 'Rejected'}]
T = Thresholds()
NOW = datetime(2026, 11, 8, 10, 0)


def dashboard(text=DASHBOARD):
    return pbx_health.parse_dashboard(m.page_to_text(text))


class ParseTests(unittest.TestCase):
    def test_dashboard(self):
        d = dashboard()
        self.assertEqual((d['cpu'], d['memory'], d['swap']), (12.5, 63.0, 0.0))
        self.assertEqual(d['disks'], [{'mount': '/', 'percent': 45.0}, {'mount': '/var/spool/asterisk', 'percent': 91.0},
                                      {'mount': '/boot', 'percent': 30.0}])  # no URL, no "1/4"
        self.assertEqual(d['services'], {'asterisk': 'running', 'mysql': 'stopped', 'apache': 'running'})
        self.assertTrue(pbx_health.has_values(d))

    def test_nothing_found_is_not_zero(self):
        d = dashboard('<html><body>Welcome to Issabel <script>var x = "CPU 99%";</script></body></html>')
        self.assertEqual((d['cpu'], d['disks'], d['services']), (None, [], {}))
        self.assertFalse(pbx_health.has_values(d))

    def test_trunks(self):
        self.assertEqual(pbx_health.parse_trunks('sip', [{'Event': 'RegistryEntry', **t} for t in TRUNKS]),
                         [{'name': 'trunk1@sip.provider.example', 'state': 'Registered', 'registered': True},
                          {'name': 'trunk2@10.1.1.1', 'state': 'Rejected', 'registered': False}])
        self.assertEqual(pbx_health.parse_trunks('pjsip', [{'ObjectName': 'itsp', 'Status': 'Unregistered'}]),
                         [{'name': 'itsp', 'state': 'Unregistered', 'registered': False}])

    def test_asterisk_state(self):
        self.assertEqual(pbx_health.asterisk_state(True, {'asterisk': 'stopped'}), 'running')  # AMI answered: it runs
        self.assertEqual(pbx_health.asterisk_state(False, {'asterisk': 'stopped'}), 'stopped')
        self.assertIsNone(pbx_health.asterisk_state(False, {}))  # a dead AMI alone proves nothing

    def test_uptime_and_thresholds(self):
        self.assertEqual(pbx_health.uptime_seconds('2026-11-08 09:00:00', NOW), 3600)
        self.assertIsNone(pbx_health.uptime_seconds(None, NOW))
        t = pbx_health.load_thresholds({'PBX_HEALTH_DISK_WARN': '70', 'PBX_HEALTH_DISK_CLEAR': '95', 'PBX_HEALTH_CPU_SAMPLES': '0',
                                        'PBX_HEALTH_ALERTS': 'off'})
        self.assertEqual((t.disk_warn, t.disk_clear, t.cpu_samples, t.alerts), (70, 70, 1, False))


class HysteresisTests(unittest.TestCase):
    def step(self, states, reading, ami_ok=True):
        return pbx_health.evaluate(states, pbx_health.observations({'ami_ok': ami_ok, **reading}, T), T, NOW, ami_ok)

    def disk(self, value):
        return {'dashboard': {'cpu': None, 'memory': None, 'swap': None, 'disks': [{'mount': '/var', 'percent': value}], 'services': {}}}

    def test_disk_band_and_counts(self):
        states = {}
        self.assertEqual(self.step(states, self.disk(86)), [])                     # one reading is not enough
        self.assertEqual(self.step(states, self.disk(87)), [('raised', 'disk', '/var', '87%')])
        for value in (84, 81, 86, 80.5):                                            # inside the dead band / still high
            self.assertEqual(self.step(states, self.disk(value)), [])
        self.assertEqual(self.step(states, self.disk(79)), [])
        self.assertEqual(self.step(states, self.disk(82)), [])                      # back in the band: count reset
        self.assertEqual(self.step(states, self.disk(79)), [])
        self.assertEqual(self.step(states, self.disk(78)), [('cleared', 'disk', '/var', '78%')])

    def test_cpu_needs_several_readings(self):
        states = {}
        cpu = lambda v: {'dashboard': {'cpu': v, 'memory': None, 'swap': None, 'disks': [], 'services': {}}}  # noqa: E731
        for value in (95, 96, 80, 95, 97):  # the 80 in the band resets the run
            self.assertEqual(self.step(states, cpu(value)), [])
        self.assertEqual(self.step(states, cpu(99))[0][0], 'raised')

    def test_trunk_down_and_back(self):
        states = {}
        down = {'trunks': [{'name': 't2', 'state': 'Rejected', 'registered': False}]}
        up = {'trunks': [{'name': 't2', 'state': 'Registered', 'registered': True}]}
        self.assertEqual(self.step(states, down), [])
        self.assertEqual(self.step(states, down), [('raised', 'trunk:t2', 't2', 'Rejected')])
        self.assertEqual(self.step(states, up), [('cleared', 'trunk:t2', 't2', 'Registered')])

    def test_lost_pbx_changes_nothing(self):
        states = {'trunk:t2': AlertState(active=True, since=NOW), 'asterisk': AlertState(active=False)}
        self.assertEqual(self.step(states, {'trunks': [], 'asterisk': None}, ami_ok=False), [])
        self.assertTrue(states['trunk:t2'].active)        # no false "registered again", no false "down"
        self.assertFalse(states['asterisk'].active)

    def test_removed_trunk_is_forgotten_quietly(self):
        states = {'trunk:old': AlertState(active=True, since=NOW)}
        self.assertEqual(self.step(states, {'trunks': []}), [])
        self.assertNotIn('trunk:old', states)

    def test_asterisk_stopped(self):
        states = {}
        for _ in range(2):
            events = self.step(states, {'asterisk': 'stopped'}, ami_ok=False)
        self.assertEqual(events, [('raised', 'asterisk', 'Asterisk', '')])
        self.assertEqual(self.step(states, {'asterisk': 'running'})[0][0], 'cleared')

    def test_alert_texts(self):
        alerts = outage_alerts.plan_health_alerts([
            {'id': 5, 'kind': 'raised', 'extension': 'trunk', 'title': 'trunk2@10.1.1.1', 'detail': 'Rejected'},
            {'id': 6, 'kind': 'cleared', 'extension': 'disk', 'title': '/var', 'detail': '78%'}], url='/x')
        self.assertEqual((alerts[0]['title'], alerts[0]['quiet'], alerts[0]['body']), ('📞 ترانک trunk2@10.1.1.1 رجیستر نیست', False, 'Rejected'))
        self.assertEqual((alerts[1]['quiet'], alerts[1]['body']), (True, '/var: 78%'))


# ---------------------------------------------------------------------------- app
class FakeScraper:
    def __init__(self, html=DASHBOARD, fail=None):
        self.html, self.fail, self.paths = html, fail, []

    def fetch_path(self, path):
        self.paths.append(path)
        if self.fail:
            raise self.fail
        return self.html


class HealthCase(AppCase):
    def setUp(self):
        super().setUp()
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        self.server = AstServer(secret=SECRET, registry=TRUNKS)
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        self.env = {'PBX_AMI_HOST': '127.0.0.1', 'PBX_AMI_PORT': str(self.server.port), 'PBX_AMI_USER': self.server.username,
                    'PBX_AMI_SECRET': SECRET, 'PBX_AMI_TIMEOUT': '2'}
        env = mock.patch.dict(os.environ, self.env)
        env.start()
        self.addCleanup(env.stop)
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            roles = {r.key: r.id for r in m.Role.query.all()}
            for name, role in (('boss', features.SUPER_ADMIN), ('op', 'operator'), ('view', 'viewer')):
                user = m.User(username=name, display_name=name, role_id=roles[role])
                user.set_password(LEGACY_PASSWORD)
                m.db.session.add(user)
            m.db.session.commit()

    def client(self, name='op'):
        client = self.app.test_client()
        client.post(f'/{self.prefix}/login', data={'username': name, 'password': LEGACY_PASSWORD})
        return client


class ReadHealthTests(HealthCase):
    def test_both_sources(self):
        reading = m.read_health(scraper=FakeScraper())
        self.assertTrue(reading['ami_ok'])
        self.assertEqual(reading['core']['current_calls'], 7)
        self.assertEqual([t['registered'] for t in reading['trunks']], [True, False])
        self.assertEqual((reading['dashboard']['cpu'], reading['asterisk']), (12.5, 'running'))
        self.assertNotIn('Command', [a['Action'] for a in self.server.seen])  # no shell-like action on AMI

    def test_ami_down_dashboard_decides(self):
        self.server.shutdown()
        self.server.server_close()
        html = DASHBOARD.replace('<td>Asterisk</td><td>Running</td>', '<td>Asterisk</td><td>Stopped</td>')
        with mock.patch.dict(os.environ, {'PBX_AMI_TIMEOUT': '1'}):
            reading = m.read_health(scraper=FakeScraper(html))
        self.assertEqual((reading['ami_ok'], reading['asterisk'], reading['ami_error'].kind), (False, 'stopped', 'unreachable'))

    def test_nothing_readable_raises(self):
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(ConnectorError):
            m.read_health(scraper=FakeScraper('<html>login</html>'))

    def test_dashboard_without_values_is_an_error_but_ami_still_counts(self):
        reading = m.read_health(scraper=FakeScraper('<html>nothing here</html>'))
        self.assertTrue(reading['ami_ok'])
        self.assertEqual((reading['dashboard'], reading['web_error'].kind), (None, 'protocol'))


class AlertTests(HealthCase):
    def reading(self, trunk_ok=True, disk=45):
        return {'ami_ok': True, 'ami_error': None, 'core': {'startup': '2026-11-08 09:00:00', 'reload': None, 'current_calls': 3},
                'driver': 'sip', 'trunks': [{'name': 't2', 'state': 'Registered' if trunk_ok else 'Rejected', 'registered': trunk_ok}],
                'dashboard': {'cpu': 10.0, 'memory': 50.0, 'swap': 0.0, 'disks': [{'mount': '/', 'percent': disk}], 'services': {}},
                'web_error': None, 'asterisk': 'running'}

    def test_events_survive_a_restart_and_reach_the_feed(self):
        with self.app.app_context():
            m.apply_health_alerts(self.reading(trunk_ok=False))
        self.start()  # restart: the count of 1 is remembered
        with self.app.app_context():
            self.assertEqual(m.apply_health_alerts(self.reading(trunk_ok=False)), [('raised', 'trunk:t2', 't2', 'Rejected')])
            self.assertEqual(m.apply_health_alerts(self.reading(trunk_ok=False)), [])  # not repeated
            event = m.OutageEvent.query.one()
            self.assertEqual((event.category, event.kind, event.extension, event.title), ('pbx', 'raised', 'trunk', 't2'))
        client = self.client()
        data = client.get('/api/pbx/health-alerts?after=0').get_json()
        self.assertEqual([a['title'] for a in data['alerts']], ['📞 ترانک t2 رجیستر نیست'])
        phone = client.get('/api/outages/alerts?after=0').get_json()
        self.assertEqual((phone['alerts'], phone['latest_id']), ([], 0))  # the phone feed never sees health events
        self.assertEqual(self.client('view').get('/api/pbx/health-alerts').status_code, 403)

    def test_alerts_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {'PBX_HEALTH_ALERTS': '0'}), self.app.app_context():
            for _ in range(3):
                m.apply_health_alerts(self.reading(trunk_ok=False, disk=99))
            self.assertEqual(m.OutageEvent.query.count(), 0)
            self.assertEqual(m.PbxAlertState.query.filter_by(active=True).count(), 2)  # the page still knows


class PageTests(HealthCase):
    def test_page_and_home_card_show_the_cached_reading(self):
        with mock.patch.object(m, 'read_health', lambda: AlertTests.reading(self, disk=91)):
            snap = pbx_snapshot.refresh(pbx_health.SNAPSHOT_NAME, force=True)
        self.assertEqual(snap.state, 'ok')
        before = len(self.server.seen)
        client = self.client()
        page = client.get(f'/{self.prefix}/pbx-health').get_data(as_text=True)
        for text in ('91%', '10%', 'در حال اجرا', 't2', 'رجیستر است', 'آخرین خواندن از ایزابل'):
            self.assertIn(text, page)
        home = client.get(f'/{self.prefix}').get_data(as_text=True)
        self.assertIn('سلامت سرور ایزابل', home)
        self.assertEqual(len(self.server.seen), before)  # pages never ask the PBX
        self.assertEqual(self.client('view').get(f'/{self.prefix}/pbx-health').status_code, 403)

    def test_unreadable_pbx_is_unknown_not_broken(self):
        self.server.shutdown()
        self.server.server_close()
        with mock.patch.dict(os.environ, {'PBX_AMI_TIMEOUT': '1'}), self.assertLogs('hotel.pbx_snapshot', 'WARNING'):
            client = self.client()
            client.post(f'/{self.prefix}/pbx-health/refresh')
        page = client.get(f'/{self.prefix}/pbx-health').get_data(as_text=True)
        self.assertIn('هنوز از ایزابل خوانده نشده است', page)
        with self.app.app_context():
            self.assertEqual(m.PbxAlertState.query.count(), 0)
        self.assertIsNone(m.pbx_sync_state['error_since'])  # no PBX-LOST for the phones


if __name__ == '__main__':
    unittest.main()
