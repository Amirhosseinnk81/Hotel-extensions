"""Tests for the PBX (Issabel / Asterisk chan_sip over AMI) link.

The AMI framing, field names and status strings follow Asterisk 18.6.0's chan_sip.c, and the
sample peers / User-Agents are taken from the hotel's real `sip show peers` / `sip show peer`
output. A small fake AMI server speaks the real protocol, so the client is tested end to end.
"""
import os
import socket
import socketserver
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

import app as m
import pbx_sync
from pbx_sync import AMIClient, AMIError, parse_peer_status, parse_user_agent, peer_from_event
from tests.test_pbx_foundation import ADMIN_PASSWORD, AppTestCase  # noqa: F401  (shared fixture)

YEALINK_UA = 'Yealink SIP-T31P 124.86.0.40'      # real: no MAC inside
HTEK_UA = 'Htek UC902 001fc11f5ddd'               # real: the MAC is the last token


def peer_event(name, ip='-none-', status='UNKNOWN', port='5060', dynamic='yes', object_type='peer'):
    """A PeerEntry event exactly as chan_sip's manager_sip_show_peers prints it."""
    return {'Event': 'PeerEntry', 'Channeltype': 'SIP', 'ObjectName': name, 'ChanObjectType': object_type,
            'IPaddress': ip, 'IPport': '0' if ip == '-none-' else port, 'Dynamic': dynamic, 'AutoForcerport': 'no',
            'Forcerport': 'no', 'AutoComedia': 'no', 'Comedia': 'no', 'VideoSupport': 'no', 'TextSupport': 'no',
            'ACL': 'yes', 'Status': status, 'RealtimeDevice': 'no', 'Description': '', 'Accountcode': ''}


# real peers from the hotel PBX, plus a few special cases
REAL_PEERS = [
    peer_event('100', '192.168.225.66', 'OK (3 ms)'),
    peer_event('101', '192.168.231.247', 'OK (5 ms)'),
    peer_event('184', '192.168.224.150', 'OK (10 ms)'),
    peer_event('1001', '192.168.224.36', 'OK (3 ms)'),
    peer_event('1002', '192.168.227.175', 'OK (5 ms)'),
    peer_event('1003', '192.168.224.77', 'LAGGED (210 ms)'),
    peer_event('1004'),                                         # never registered / expired
    peer_event('1005', '192.168.225.131', 'UNREACHABLE'),
    peer_event('777', '192.168.226.10', 'OK (6 ms)'),           # numeric but not a room, not in the directory
    peer_event('main-trunk', '10.0.0.9', 'OK (20 ms)', dynamic='no'),
]
AGENTS = {'100': YEALINK_UA, '101': YEALINK_UA, '184': HTEK_UA, '1001': YEALINK_UA, '1002': YEALINK_UA, '1003': YEALINK_UA,
          '1005': YEALINK_UA, '777': 'Zoiper rv2.10'}


class FakeClient:
    """Stands in for AMIClient in the database tests."""

    def __init__(self, events=REAL_PEERS, agents=AGENTS):
        self.events, self.agents, self.show_calls = events, agents, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_action(self, name, **fields):
        assert name == 'SIPpeers'
        return list(self.events)

    def action(self, name, **fields):
        assert name == 'SIPshowpeer'
        self.show_calls.append(fields['Peer'])
        agent = self.agents.get(fields['Peer'])
        if agent is None:
            return {'Response': 'Error', 'Message': 'Peer not found'}
        return {'Response': 'Success', 'SIP-Useragent': agent, 'Reg-Contact': 'sip:x@1.2.3.4:5060'}


# ---------------------------------------------------------------------------- fake AMI server
class FakeAMIHandler(socketserver.StreamRequestHandler):
    def send(self, text):
        data = text.encode('utf-8')
        if self.server.drip:
            for i in range(0, len(data), 7):
                self.wfile.write(data[i:i + 7])
                self.wfile.flush()
                time.sleep(0.001)
        else:
            self.wfile.write(data)
            self.wfile.flush()

    def read_action(self):
        lines = []
        while True:
            line = self.rfile.readline()
            if not line:
                return None
            line = line.decode('utf-8').rstrip('\r\n')
            if line == '':
                if lines:
                    break
                continue
            lines.append(line)
        return {k.strip(): v.strip() for k, _, v in (l.partition(':') for l in lines)}

    def handle(self):
        server = self.server
        if server.mode == 'bad_banner':
            self.send('HTTP/1.1 400 Bad Request\r\n')
            return
        self.send('Asterisk Call Manager/9.0.0\r\n')
        if server.mode == 'silent':
            time.sleep(2)
            return
        while True:
            action = self.read_action()
            if action is None:
                return
            name = action.get('Action')
            server.seen.append(action)
            if name == 'Login':
                ok = action.get('Username') == server.username and action.get('Secret') == server.secret
                self.send('Response: Success\r\nMessage: Authentication accepted\r\n\r\n' if ok
                          else 'Response: Error\r\nMessage: Authentication failed\r\n\r\n')
                if not ok:
                    return
            elif name == 'SIPpeers':
                self.send('Response: Success\r\nEventList: start\r\nMessage: Peer status list will follow\r\n\r\n')
                for event in server.events:
                    body = ''.join(f'{k}: {v}\r\n' for k, v in event.items())
                    self.send(body + '\r\n')
                self.send(f'Event: PeerlistComplete\r\nEventList: Complete\r\nListItems: {len(server.events)}\r\n\r\n')
            elif name == 'SIPshowpeer':
                agent = server.agents.get(action.get('Peer'))
                if agent is None:
                    self.send('Response: Error\r\nMessage: Peer not found\r\n\r\n')
                else:
                    self.send(f"Response: Success\r\nChannelType: SIP\r\nObjectName: {action['Peer']}\r\nSecretExist: Y\r\n"
                              f'Status: OK (5 ms)\r\nSIP-Useragent: {agent}\r\nReg-Contact: sip:x@1.2.3.4:5060\r\n\r\n')
            elif name == 'Logoff':
                self.send('Response: Goodbye\r\nMessage: Thanks for all the fish.\r\n\r\n')
                return


class FakeAMIServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, events=REAL_PEERS, agents=AGENTS, username='hotelapp', secret='s3cret-value', mode='normal', drip=False):
        super().__init__(('127.0.0.1', 0), FakeAMIHandler)
        self.events, self.agents, self.username, self.secret = events, agents, username, secret
        self.mode, self.drip, self.seen = mode, drip, []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def handle_error(self, request, client_address):  # clients hanging up early is normal here
        pass

    @property
    def port(self):
        return self.server_address[1]


class AMIServerCase(unittest.TestCase):
    def serve(self, **kwargs):
        server = FakeAMIServer(**kwargs)
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server

    def client(self, server, **kwargs):
        kwargs.setdefault('timeout', 2.0)
        return AMIClient('127.0.0.1', server.port, kwargs.pop('username', server.username), kwargs.pop('secret', server.secret), **kwargs)


# ---------------------------------------------------------------------------- protocol tests
class AMIClientTests(AMIServerCase):
    def test_lists_peers(self):
        server = self.serve()
        with self.client(server) as client:
            peers = pbx_sync.fetch_peers(client)
        self.assertEqual(len(peers), len(REAL_PEERS))
        self.assertEqual((peers['101'].state, peers['101'].rtt_ms, peers['101'].ip), ('online', 5, '192.168.231.247'))
        self.assertEqual(peers['1004'].state, 'offline')
        self.assertEqual(peers['1004'].ip, '')
        login = server.seen[0]
        self.assertEqual(login['Events'], 'off')  # no event flood, no read permission needed

    def test_works_when_the_pbx_answers_in_tiny_pieces(self):
        server = self.serve(drip=True)
        with self.client(server) as client:
            self.assertEqual(len(pbx_sync.fetch_peers(client)), len(REAL_PEERS))
            self.assertEqual(pbx_sync.fetch_user_agent(client, '184'), HTEK_UA)

    def test_user_agent_of_unknown_peer_is_empty(self):
        server = self.serve()
        with self.client(server) as client:
            self.assertEqual(pbx_sync.fetch_user_agent(client, '4242'), '')

    def test_wrong_secret_is_reported_without_leaking_it(self):
        server = self.serve()
        with self.assertRaises(AMIError) as ctx:
            self.client(server, secret='wrong-password-123').connect()
        self.assertIn('login failed', str(ctx.exception))
        self.assertNotIn('wrong-password-123', str(ctx.exception))

    def test_connection_refused(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with self.assertRaises(AMIError) as ctx:
            AMIClient('127.0.0.1', port, 'u', 'p', timeout=1).connect()
        self.assertIn('cannot connect', str(ctx.exception))

    def test_silent_pbx_times_out(self):
        server = self.serve(mode='silent')
        started = time.monotonic()
        with self.assertRaises(AMIError):
            self.client(server, timeout=0.3).connect()
        self.assertLess(time.monotonic() - started, 1.5)

    def test_not_an_ami_port(self):
        server = self.serve(mode='bad_banner')
        with self.assertRaises(AMIError) as ctx:
            self.client(server).connect()
        self.assertIn('unexpected banner', str(ctx.exception))

    def test_line_breaks_cannot_be_injected_into_actions(self):
        server = self.serve()
        with self.client(server) as client:
            with self.assertRaises(AMIError):
                client.action('SIPshowpeer', Peer='101\r\nAction: Command')


class ParsingTests(unittest.TestCase):
    def test_status_texts(self):
        cases = [('OK (3 ms)', True, ('online', 3)), ('LAGGED (210 ms)', True, ('online', 210)), ('UNREACHABLE', True, ('offline', None)),
                 ('UNKNOWN', False, ('offline', None)), ('UNKNOWN', True, ('unknown', None)),
                 ('Unmonitored', True, ('online', None)), ('Unmonitored', False, ('offline', None)), ('', False, ('unknown', None))]
        for text, has_ip, expected in cases:
            self.assertEqual(parse_peer_status(text, has_ip), expected, text)

    def test_real_user_agents(self):
        yealink = parse_user_agent(YEALINK_UA)
        self.assertEqual((yealink['vendor'], yealink['model'], yealink['firmware'], yealink['mac'], yealink['display_model']),
                         ('Yealink', 'SIP-T31P', '124.86.0.40', None, 'Yealink T31P'))
        htek = parse_user_agent(HTEK_UA)
        self.assertEqual((htek['vendor'], htek['model'], htek['mac'], htek['display_model']), ('Htek', 'UC902', '00:1F:C1:1F:5D:DD', 'Htek UC902'))
        self.assertEqual(parse_user_agent('Htek UC926 2.0.4.2.5 001fc11f5ddd')['firmware'], '2.0.4.2.5')
        self.assertIsNone(parse_user_agent('Zoiper rv2.10')['mac'])
        self.assertIsNone(parse_user_agent('')['vendor'])

    def test_peer_events(self):
        self.assertIsNone(peer_from_event({'Event': 'PeerlistComplete'}))
        self.assertIsNone(peer_from_event(peer_event('1', object_type='user')))
        self.assertEqual(peer_from_event(peer_event('1004')).ip, '')


# ---------------------------------------------------------------------------- database tests
class SyncCase(AppTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'PBX_SYNC_AUTOSTART': '0'})
        patcher.start()
        self.addCleanup(patcher.stop)
        m.pbx_sync_state.update(running=False, last_attempt=None, last_success=None, last_error=None, last_duration=None, summary=None)
        m._ua_cursor['offset'] = 0
        super().setUp()
        self.add('100', 'Call Center')
        self.add('101', 'Management', last_ip='10.1.1.1', reg_status='offline', status_updated_at=datetime.utcnow() - timedelta(hours=1))
        self.add('184', 'Accounting')
        self.add('150', 'Old Number', reg_status='online', status_updated_at=datetime.utcnow(), last_ip='10.9.9.9')  # not on the PBX
        self.add('1006', 'Trashed Room', kind='room', is_deleted=True)

    def sync(self, **kwargs):
        with self.app.app_context():
            return m.sync_pbx_once(lambda: FakeClient(**kwargs))


class SyncTests(SyncCase):
    def test_existing_extensions_get_live_data(self):
        summary = self.sync()
        e101 = self.get_ext('101')
        self.assertEqual((e101['reg_status'], e101['rtt_ms'], e101['last_ip']), ('online', 5, '192.168.231.247'))
        self.assertEqual(e101['user_agent'], YEALINK_UA)
        self.assertEqual(e101['phone_model'], 'Yealink T31P')
        self.assertIsNone(e101['mac'])                      # Yealink does not announce its MAC
        self.assertIsNotNone(e101['last_seen_at'])
        e184 = self.get_ext('184')
        self.assertEqual((e184['mac'], e184['phone_model']), ('00:1F:C1:1F:5D:DD', 'Htek UC902'))
        self.assertEqual(summary['ignored'], 1)             # the trunk
        self.assertEqual(self.get_ext('100')['kind'], 'staff')

    def test_new_extensions_are_created_hidden(self):
        summary = self.sync()
        self.assertEqual(summary['created'], 6)  # 1001-1005 rooms, 777 pending (1006 is trashed)
        for number in ('1001', '1002', '1003', '1004', '1005'):
            row = self.get_ext(number)
            self.assertEqual((row['kind'], row['title']), ('room', f'اتاق {number}'))
        pending = self.get_ext('777')
        self.assertEqual((pending['kind'], pending['title']), ('pending', 'داخلی 777'))
        html = self.anon.get('/').get_data(as_text=True)
        for hidden in ('اتاق 1001', 'داخلی 777', '192.168.224.36', '192.168.226.10'):
            self.assertNotIn(hidden, html)

    def test_states_for_every_kind_of_status(self):
        self.sync()
        self.assertEqual(self.get_ext('1003')['reg_status'], 'online')      # LAGGED still answers
        self.assertEqual(self.get_ext('1003')['rtt_ms'], 210)
        self.assertEqual(self.get_ext('1004')['reg_status'], 'offline')     # not registered
        self.assertEqual(self.get_ext('1005')['reg_status'], 'offline')     # UNREACHABLE
        self.assertEqual(self.get_ext('1005')['last_ip'], '192.168.225.131')

    def test_offline_phone_keeps_its_last_ip(self):
        self.sync()
        events = [e for e in REAL_PEERS if e['ObjectName'] != '1002'] + [peer_event('1002')]
        self.sync(events=events)
        row = self.get_ext('1002')
        self.assertEqual((row['reg_status'], row['last_ip'], row['rtt_ms']), ('offline', '192.168.227.175', None))

    def test_trashed_and_missing_extensions(self):
        events = REAL_PEERS + [peer_event('1006', '192.168.224.99', 'OK (4 ms)')]
        summary = self.sync(events=events)
        self.assertEqual(summary['in_trash'], 1)
        self.assertIsNone(self.get_ext('1006')['reg_status'])               # trashed rows are left alone
        row = self.get_ext('150')                                           # no longer on the PBX: stale state dropped
        self.assertEqual((row['reg_status'], row['status_updated_at']), (None, None))
        self.assertGreaterEqual(summary['not_on_pbx'], 1)

    def test_admin_values_are_never_overwritten(self):
        with self.app.app_context():
            e = m.Extension.query.filter_by(extension='184').first()
            e.phone_model, e.mac = 'Custom Model', 'AA:AA:AA:AA:AA:AA'
            m.db.session.commit()
        self.sync()
        row = self.get_ext('184')
        self.assertEqual((row['phone_model'], row['mac']), ('Custom Model', 'AA:AA:AA:AA:AA:AA'))
        html = self.admin.get(self.url('/pbx')).get_data(as_text=True)
        self.assertIn('00:1F:C1:1F:5D:DD', html)                            # flagged as a mismatch
        self.assertIn('AA:AA:AA:AA:AA:AA', html)

    def test_sync_does_not_touch_updated_at(self):
        before = self.get_ext('101')['updated_at']
        self.sync()
        self.assertEqual(self.get_ext('101')['updated_at'], before)
        created_row = self.get_ext('1001')['updated_at']
        self.sync()
        self.assertEqual(self.get_ext('1001')['updated_at'], created_row)
        self.assertEqual(self.get_ext('101')['updated_at'], before)

    def test_second_sync_changes_nothing_new(self):
        self.sync()
        summary = self.sync()
        self.assertEqual(summary['created'], 0)

    def test_user_agents_are_read_in_batches(self):
        with mock.patch.dict(os.environ, {'PBX_UA_BATCH': '3'}):
            clients = []

            def factory():
                clients.append(FakeClient())
                return clients[-1]

            with self.app.app_context():
                m.sync_pbx_once(factory)
                self.assertEqual(len(clients[0].show_calls), 3)
                for _ in range(6):
                    m.sync_pbx_once(factory)
        with self.app.app_context():
            missing = [e.extension for e in m.Extension.query.filter(m.Extension.user_agent == None, m.Extension.reg_status == 'online')]  # noqa: E711
        self.assertEqual(missing, [])

    def test_changed_address_triggers_a_fresh_user_agent(self):
        self.sync()
        events = [peer_event('101', '192.168.231.250', 'OK (5 ms)') if e['ObjectName'] == '101' else e for e in REAL_PEERS]
        with mock.patch.dict(os.environ, {'PBX_UA_BATCH': '1'}):
            client = FakeClient(events=events, agents={**AGENTS, '101': 'Yealink SIP-T31P 124.86.0.99'})
            with self.app.app_context():
                m.sync_pbx_once(lambda: client)
        self.assertEqual(client.show_calls, ['101'])
        self.assertEqual(self.get_ext('101')['user_agent'], 'Yealink SIP-T31P 124.86.0.99')

    def test_public_page_shows_staff_state_only(self):
        self.sync()
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn(m.PHONE_STATUS_LABELS['online'], html)
        for secret in ('192.168.231.247', '00:1F:C1:1F:5D:DD', 'Yealink', 'Htek'):
            self.assertNotIn(secret, html)


class RunnerTests(SyncCase):
    def test_failure_is_recorded_then_cleared(self):
        def broken():
            raise AMIError('cannot connect to 192.168.228.2:5038 (TimeoutError)')

        ok, message = m.run_pbx_sync(self.app, broken)
        self.assertFalse(ok)
        self.assertIn('cannot connect', message)
        self.assertEqual(m.pbx_sync_state['last_error'], message)
        ok, summary = m.run_pbx_sync(self.app, lambda: FakeClient())
        self.assertTrue(ok)
        self.assertIsNone(m.pbx_sync_state['last_error'])
        self.assertIsNotNone(m.pbx_sync_state['last_success'])
        self.assertEqual(summary['online'], m.pbx_sync_state['summary']['online'])

    def test_unexpected_errors_do_not_kill_the_runner(self):
        def explode():
            raise ValueError('boom')

        with self.assertLogs(self.app.logger, level='ERROR'):
            ok, message = m.run_pbx_sync(self.app, explode)
        self.assertFalse(ok)
        self.assertNotIn('boom', message)
        self.assertFalse(m.pbx_sync_lock.locked())

    def test_overlapping_runs_are_refused(self):
        with m.pbx_sync_lock:
            ok, message = m.run_pbx_sync(self.app, lambda: FakeClient())
        self.assertFalse(ok)
        self.assertIn('در حال اجرا', message)

    def test_thread_only_starts_when_configured(self):
        with mock.patch.dict(os.environ, {'PBX_AMI_HOST': '', 'PBX_AMI_USER': '', 'PBX_AMI_SECRET': ''}):
            self.assertIsNone(m.start_pbx_sync_thread(self.app))
        self.assertNotIn('pbx_sync_stop', self.app.extensions)


class PbxPageTests(SyncCase):
    def env(self, server=None):
        values = {'PBX_AMI_HOST': '127.0.0.1', 'PBX_AMI_PORT': str(server.port if server else 1), 'PBX_AMI_USER': 'hotelapp', 'PBX_AMI_SECRET': 's3cret-value'}
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_page_requires_login(self):
        for path in ('/pbx',):
            self.assertEqual(self.anon.get(self.url(path)).status_code, 302)
        self.assertEqual(self.anon.post(self.url('/pbx/sync')).status_code, 302)
        self.assertEqual(self.anon.post(self.url('/pbx/set-kind'), data={'id': '1', 'kind': 'room'}).status_code, 302)

    def test_page_without_configuration(self):
        html = self.admin.get(self.url('/pbx')).get_data(as_text=True)
        self.assertIn('تنظیم نشده', html)
        response = self.admin.post(self.url('/pbx/sync'), follow_redirects=True)
        self.assertIn('تنظیم نشده', response.get_data(as_text=True))

    def test_sync_button_with_a_real_ami_connection(self):
        server = FakeAMIServer()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        self.env(server)
        response = self.admin.post(self.url('/pbx/sync'), follow_redirects=True)
        text = response.get_data(as_text=True)
        self.assertIn('همگام‌سازی انجام شد', text)
        self.assertEqual(self.get_ext('184')['mac'], '00:1F:C1:1F:5D:DD')
        self.assertEqual(self.get_ext('1001')['kind'], 'room')
        self.assertIn('127.0.0.1:' + str(server.port), text)
        self.assertNotIn('s3cret-value', text)               # the AMI secret never reaches a page

    def test_failed_sync_is_explained(self):
        self.env()  # port 1: nothing listens there
        text = self.admin.post(self.url('/pbx/sync'), follow_redirects=True).get_data(as_text=True)
        self.assertIn('همگام‌سازی ناموفق بود', text)
        self.assertIn('cannot connect', text)
        self.assertNotIn('s3cret-value', text)

    def test_review_queue_and_set_kind(self):
        self.sync()
        html = self.admin.get(self.url('/pbx')).get_data(as_text=True)
        self.assertIn('در انتظار تأیید', html)
        self.assertIn('777', html)
        entry_id = self.get_ext('777')['id']
        self.admin.post(self.url('/pbx/set-kind'), data={'id': str(entry_id), 'kind': 'staff'})
        self.assertEqual(self.get_ext('777')['kind'], 'staff')
        self.assertIn('داخلی 777', self.anon.get('/').get_data(as_text=True))   # now visible to staff
        self.admin.post(self.url('/pbx/set-kind'), data={'id': str(entry_id), 'kind': 'pending'})  # not allowed from this form
        self.assertEqual(self.get_ext('777')['kind'], 'staff')
        self.admin.post(self.url('/pbx/set-kind'), data={'id': '99999', 'kind': 'room'})

    def test_offline_and_not_on_pbx_lists(self):
        with self.app.app_context():
            m.pbx_sync_state['last_success'] = datetime.utcnow()
        self.sync()
        with self.app.app_context():
            m.pbx_sync_state['last_success'] = datetime.utcnow()
        html = self.admin.get(self.url('/pbx')).get_data(as_text=True)
        self.assertIn('تلفن‌های آفلاین', html)
        self.assertIn('1004', html)
        self.assertIn('روی PBX دیده نشد', html)
        self.assertIn('150', html)

    def test_admin_panel_links_to_the_pbx_page(self):
        self.assertIn(self.url('/pbx'), self.admin.get(self.url('')).get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
