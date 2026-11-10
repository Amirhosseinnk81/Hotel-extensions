"""Tests for the shared PBX connection layer (pbx_connectors.py) and the snapshot cache (pbx_snapshot.py).

No real PBX is needed: AMI is tested end to end against the fake AMI server of test_pbx_sync (extended
through its `answer` hook with DBGet / CoreStatus / registry answers shaped like Asterisk 18.6's
manager.c and chan_sip.c), MySQL with a fake connection object. What these fakes cannot prove (the
real privilege classes of the AMI user, the real MariaDB 5.5 behaviour) is checked on the PBX with
tools/check_pbx.py.
"""
import contextlib
import importlib.util
import io
import os
import re
import socket
import sys
import time
import unittest
from datetime import datetime
from unittest import mock

import pbx_connectors as pc
import pbx_snapshot
from pbx_connectors import AmiReader, AmiSettings, CdrClient, CdrSettings, ConnectorError, Settings, load_settings
from tests.test_pbx_sync import FakeAMIServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SECRET = 's3cret-value'


def load_tool(name):
    spec = importlib.util.spec_from_file_location(f'tool_{name}', os.path.join(ROOT, 'tools', f'{name}.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AstServer(FakeAMIServer):
    """The fake AMI server plus the read-only actions the new features use."""

    def __init__(self, astdb=None, driver='sip', old_dbget=False, denied=(), registry=(), ignore_writes=False, fail_devstate=False, **kwargs):
        super().__init__(**kwargs)
        self.astdb = dict(astdb or {})
        self.driver, self.old_dbget, self.denied, self.registry = driver, old_dbget, set(denied), list(registry)
        self.ignore_writes, self.fail_devstate, self.devstates = ignore_writes, fail_devstate, {}

    def answer(self, action):
        name = action.get('Action')
        if name in self.denied:
            return 'Response: Error\r\nMessage: Permission denied\r\n\r\n'
        if name == 'Setvar':  # no Channel: writes a global variable / function, like Asterisk 18
            variable, value = action.get('Variable', ''), action.get('Value', '')
            db = re.match(r'^DB\(([^/()]+)/([^/()]+)\)$', variable)
            dev = re.match(r'^DEVICE_STATE\(Custom:([^()]+)\)$', variable)
            if dev and self.fail_devstate:
                return 'Response: Error\r\nMessage: Failed to set variable\r\n\r\n'
            if not self.ignore_writes:
                if db:
                    self.astdb[(db.group(1), db.group(2))] = value
                elif dev:
                    self.devstates[dev.group(1)] = value
            return 'Response: Success\r\nMessage: Variable Set\r\n\r\n'
        if name == 'DBPut':
            if not self.ignore_writes:
                self.astdb[(action.get('Family'), action.get('Key'))] = action.get('Val', '')
            return 'Response: Success\r\nMessage: Updated database successfully\r\n\r\n'
        if name == 'DBGet':
            value = self.astdb.get((action.get('Family'), action.get('Key')))
            if value is None:
                return 'Response: Error\r\nMessage: Database entry not found\r\n\r\n'
            event = f"Event: DBGetResponse\r\nFamily: {action['Family']}\r\nKey: {action['Key']}\r\nVal: {value}\r\n\r\n"
            if self.old_dbget:  # older Asterisk: plain Success, one event, no list framing
                return 'Response: Success\r\nMessage: Result will follow\r\n\r\n' + event
            return ('Response: Success\r\nEventList: start\r\nMessage: Result will follow\r\n\r\n' + event
                    + 'Event: DBGetComplete\r\nEventList: Complete\r\nListItems: 1\r\n\r\n')
        if name == 'CoreStatus':
            return ('Response: Success\r\nCoreStartupDate: 2026-10-01\r\nCoreStartupTime: 03:15:00\r\n'
                    'CoreReloadDate: 2026-10-20\r\nCoreReloadTime: 11:00:05\r\nCoreCurrentCalls: 7\r\n\r\n')
        if name == 'SIPshowpeer' and self.driver != 'sip':
            return 'Response: Error\r\nMessage: Invalid/unknown command\r\n\r\n'
        if name == 'PJSIPShowEndpoint':
            if self.driver == 'pjsip':
                return 'Response: Error\r\nMessage: Unable to retrieve endpoint hotelapp-driver-probe\r\n\r\n'
            return 'Response: Error\r\nMessage: Invalid/unknown command\r\n\r\n'
        if name == 'SIPshowregistry':
            body = 'Response: Success\r\nEventList: start\r\nMessage: Registrations will follow\r\n\r\n'
            for entry in self.registry:
                body += 'Event: RegistryEntry\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in entry.items()) + '\r\n'
            return body + f'Event: RegistrationsComplete\r\nEventList: Complete\r\nListItems: {len(self.registry)}\r\n\r\n'
        return None


class AmiCase(unittest.TestCase):
    def setUp(self):
        pc.forget_detected_driver()
        self.addCleanup(pc.forget_detected_driver)

    def serve(self, **kwargs):
        server = AstServer(secret=SECRET, **kwargs)
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server

    def settings(self, server=None, port=None, driver='auto', **ami):
        ami.setdefault('timeout', 2.0)
        return Settings(ami=AmiSettings(host='127.0.0.1', port=port or server.port, user=server.username if server else 'hotelapp',
                                        secret=ami.pop('secret', SECRET), **ami), cdr=CdrSettings(), sip_driver=driver)


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        s = load_settings({})
        self.assertFalse(s.ami.ready)
        self.assertFalse(s.cdr.ready)
        self.assertEqual((s.ami.port, s.ami.auth, s.cdr.port, s.cdr.database, s.sip_driver, s.snapshot_ttl),
                         (5038, 'md5', 3306, 'asteriskcdrdb', 'auto', 120))

    def test_values_and_limits(self):
        s = load_settings({'PBX_AMI_HOST': ' 10.0.0.2 ', 'PBX_AMI_USER': 'u', 'PBX_AMI_SECRET': 'x', 'PBX_AMI_AUTH': 'PLAIN',
                           'PBX_SIP_DRIVER': 'PJSIP', 'PBX_SNAPSHOT_TTL': '5', 'PBX_CDR_HOST': 'db', 'PBX_CDR_USER': 'r',
                           'PBX_CDR_PASS': 'p', 'PBX_CDR_PORT': 'oops', 'PBX_AMI_TIMEOUT': '0'})
        self.assertTrue(s.ami.ready and s.cdr.ready)
        self.assertEqual((s.ami.host, s.ami.auth, s.sip_driver, s.snapshot_ttl, s.cdr.port, s.ami.timeout),
                         ('10.0.0.2', 'plain', 'pjsip', 30, 3306, 1.0))

    def test_unknown_driver_falls_back_to_auto(self):
        self.assertEqual(load_settings({'PBX_SIP_DRIVER': 'iax'}).sip_driver, 'auto')

    def test_secrets_are_not_in_the_repr(self):
        s = load_settings({'PBX_AMI_SECRET': 'ami-very-secret', 'PBX_CDR_PASS': 'db-very-secret'})
        self.assertNotIn('ami-very-secret', repr(s))
        self.assertNotIn('db-very-secret', repr(s))


class AmiReaderTests(AmiCase):
    def test_not_configured(self):
        with self.assertRaises(ConnectorError) as ctx:
            AmiReader(Settings(ami=AmiSettings(), cdr=CdrSettings())).__enter__()
        self.assertEqual(ctx.exception.kind, 'config')
        self.assertIn('.env', ctx.exception.fa)

    def test_md5_login_and_core_status(self):
        server = self.serve()
        with AmiReader(self.settings(server)) as ami:
            status = ami.core_status()
        self.assertEqual(status, {'startup': '2026-10-01 03:15:00', 'reload': '2026-10-20 11:00:05', 'current_calls': 7})
        self.assertEqual(server.seen[0]['Action'], 'Challenge')
        self.assertNotIn(SECRET, str(server.seen))
        deadline = time.monotonic() + 2  # the fake server records the Logoff on its own thread
        while server.seen[-1]['Action'] != 'Logoff' and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(server.seen[-1]['Action'], 'Logoff')  # the connection is closed again

    def test_plain_login_still_possible(self):
        server = self.serve()
        with AmiReader(self.settings(server, auth='plain')) as ami:
            ami.core_status()
        login = [a for a in server.seen if a['Action'] == 'Login'][0]
        self.assertEqual(login.get('Secret'), SECRET)
        self.assertNotIn('Challenge', [a['Action'] for a in server.seen])

    def test_refused_challenge_is_an_auth_error(self):
        server = self.serve(denied={'Challenge'})
        with self.assertRaises(ConnectorError) as ctx:
            AmiReader(self.settings(server)).__enter__()
        self.assertEqual(ctx.exception.kind, 'auth')

    def test_wrong_secret(self):
        server = self.serve()
        with self.assertRaises(ConnectorError) as ctx:
            AmiReader(self.settings(server, secret='wrong-password-123')).__enter__()
        self.assertEqual(ctx.exception.kind, 'auth')
        self.assertNotIn('wrong-password-123', str(ctx.exception) + ctx.exception.fa)

    def test_unreachable(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with self.assertRaises(ConnectorError) as ctx:
            AmiReader(self.settings(port=port, timeout=1.0)).__enter__()
        self.assertEqual(ctx.exception.kind, 'unreachable')
        self.assertIn('در دسترس نیست', ctx.exception.fa)

    def test_silent_pbx_is_a_timeout(self):
        server = self.serve(mode='silent')
        with self.assertRaises(ConnectorError) as ctx:
            AmiReader(self.settings(server, timeout=1.0)).__enter__()
        self.assertEqual(ctx.exception.kind, 'timeout')

    def test_db_get(self):
        server = self.serve(astdb={('DND', '201'): 'YES', ('CF', '305'): '0912xxxx'})
        with AmiReader(self.settings(server)) as ami:
            self.assertEqual(ami.db_get('DND', '201'), 'YES')
            self.assertIsNone(ami.db_get('DND', '202'))
            self.assertEqual(ami.db_get_many('DND', ['201', '202', '203']), {'201': 'YES'})
            self.assertEqual(ami.db_get('CF', '305'), '0912xxxx')
            self.assertEqual(ami.core_status()['current_calls'], 7)  # the stream stays in step after list answers

    def test_db_get_without_list_framing(self):
        server = self.serve(astdb={('DND', '201'): 'YES'}, old_dbget=True)
        with AmiReader(self.settings(server)) as ami:
            self.assertEqual(ami.db_get('DND', '201'), 'YES')
            self.assertIsNone(ami.db_get('DND', '999'))

    def test_db_get_rejects_odd_names(self):
        server = self.serve()
        with AmiReader(self.settings(server)) as ami:
            for family, key in (('DND', '1 2'), ('DND/../x', '1'), ('', '1'), ('DND', 'a\r\nAction: Originate')):
                with self.assertRaises(ValueError):
                    ami.db_get(family, key)

    def test_permission_denied(self):
        server = self.serve(denied={'DBGet', 'CoreStatus'})
        with AmiReader(self.settings(server)) as ami:
            for call in (lambda: ami.db_get('DND', '201'), ami.core_status):
                with self.assertRaises(ConnectorError) as ctx:
                    call()
                self.assertEqual(ctx.exception.kind, 'permission')

    def test_driver_detection(self):
        for driver in ('sip', 'pjsip'):
            pc.forget_detected_driver()
            server = self.serve(driver=driver)
            with AmiReader(self.settings(server)) as ami:
                self.assertEqual(ami.driver(), driver)

    def test_driver_detected_once(self):
        server = self.serve()
        for _ in range(2):
            with AmiReader(self.settings(server)) as ami:
                ami.driver()
        self.assertEqual(sum(1 for a in server.seen if a['Action'] == 'SIPshowpeer'), 1)

    def test_no_driver(self):
        server = self.serve(driver='none')
        with AmiReader(self.settings(server)) as ami:
            with self.assertRaises(ConnectorError) as ctx:
                ami.driver()
        self.assertEqual(ctx.exception.kind, 'driver')

    def test_configured_driver_needs_no_probe(self):
        server = self.serve(driver='none')
        with AmiReader(self.settings(server, driver='pjsip')) as ami:
            self.assertEqual(ami.driver(), 'pjsip')
        self.assertFalse([a for a in server.seen if a['Action'] in ('SIPshowpeer', 'PJSIPShowEndpoint')])

    def test_sip_registry(self):
        entries = [{'Host': 'sip.provider.example', 'Port': '5060', 'Username': 'trunk1', 'State': 'Registered'},
                   {'Host': '10.1.1.1', 'Port': '5060', 'Username': 'trunk2', 'State': 'Rejected'}]
        server = self.serve(registry=entries)
        with AmiReader(self.settings(server)) as ami:
            driver, events = ami.registry()
        self.assertEqual(driver, 'sip')
        self.assertEqual([(e['Username'], e['State']) for e in events], [('trunk1', 'Registered'), ('trunk2', 'Rejected')])


# ---------------------------------------------------------------------------- CDR
class FakeMySQLError(Exception):
    pass


FakeMySQLError.__module__ = 'pymysql.err'  # how CdrClient recognises PyMySQL's errors


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.connection.executed.append((sql, params))
        if self.connection.fail_with:
            raise self.connection.fail_with

    def fetchmany(self, size):
        return self.connection.rows[:size]


class FakeConnection:
    def __init__(self, rows=(), fail_with=None):
        self.rows, self.fail_with = list(rows), fail_with
        self.executed, self.closed = [], False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


CDR = Settings(ami=AmiSettings(), cdr=CdrSettings(host='db', user='hotel_cdr', password='db-secret-pw'))


class CdrClientTests(unittest.TestCase):
    def client(self, connection=None, settings=CDR):
        self.connection = connection or FakeConnection()
        self.connect_calls = []

        def connect(cdr):
            self.connect_calls.append(cdr)
            if isinstance(self.connection, Exception):
                raise self.connection
            return self.connection
        return CdrClient(settings, connect=connect)

    def test_select_with_parameters_and_row_cap(self):
        client = self.client(FakeConnection(rows=[{'n': i} for i in range(10)]))
        rows = client.select('SELECT n FROM cdr WHERE calldate >= %s', ('2026-11-01',), max_rows=3)
        self.assertEqual(rows, [{'n': 0}, {'n': 1}, {'n': 2}])
        self.assertEqual(self.connection.executed, [('SELECT n FROM cdr WHERE calldate >= %s', ('2026-11-01',))])
        self.assertTrue(self.connection.closed)

    def test_only_a_single_select(self):
        client = self.client()
        for sql in ('DELETE FROM cdr', 'UPDATE cdr SET src=1', 'SELECT 1; DROP TABLE cdr', '  drop table cdr', ''):
            with self.assertRaises(ValueError):
                client.select(sql)
        self.assertEqual(self.connect_calls, [])  # refused before connecting

    def test_not_configured(self):
        with self.assertRaises(ConnectorError) as ctx:
            self.client(settings=Settings(ami=AmiSettings(), cdr=CdrSettings())).select('SELECT 1')
        self.assertEqual(ctx.exception.kind, 'config')

    def test_mysql_errors_are_classified_and_closed(self):
        cases = ((1045, "Access denied for user 'hotel_cdr'@'10.0.0.5' (using password: YES)", 'auth'),
                 (1142, 'SELECT command denied to user', 'permission'),
                 (2013, 'Lost connection to MySQL server during query', 'timeout'),
                 (1064, 'You have an error in your SQL syntax', 'protocol'))
        for code, message, kind in cases:
            client = self.client(FakeConnection(fail_with=FakeMySQLError(code, message)))
            with self.assertRaises(ConnectorError) as ctx:
                client.select('SELECT 1')
            self.assertEqual(ctx.exception.kind, kind, code)
            self.assertNotIn('db-secret-pw', str(ctx.exception))
            self.assertTrue(self.connection.closed)

    def test_cannot_connect(self):
        client = self.client(FakeMySQLError(2003, "Can't connect to MySQL server on 'db' (timed out)"))
        with self.assertRaises(ConnectorError) as ctx:
            client.select('SELECT 1')
        self.assertEqual(ctx.exception.kind, 'unreachable')
        client = self.client(socket.timeout('timed out'))
        with self.assertRaises(ConnectorError) as ctx:
            client.select('SELECT 1')
        self.assertEqual(ctx.exception.kind, 'timeout')

    def test_missing_pymysql(self):
        with mock.patch.dict(sys.modules, {'pymysql': None, 'pymysql.cursors': None}):
            with self.assertRaises(ConnectorError) as ctx:
                CdrClient(CDR).select('SELECT 1')
        self.assertEqual(ctx.exception.kind, 'dependency')

    def test_ping(self):
        class Answers(FakeConnection):
            def cursor(inner):
                cursor = FakeCursor(inner)
                cursor.fetchmany = lambda size: ([{'version': '5.5.68-MariaDB', 'now': datetime(2026, 11, 6, 9, 0)}]
                                                 if 'VERSION' in inner.executed[-1][0] else [{'calldate': datetime(2026, 11, 6, 8, 59)}])
                return cursor
        info = self.client(Answers()).ping()
        self.assertEqual(info['version'], '5.5.68-MariaDB')
        self.assertEqual(info['latest_call'], datetime(2026, 11, 6, 8, 59))


# ---------------------------------------------------------------------------- snapshot cache
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        self.clock = Clock()
        self.calls = 0
        self.result = {'201': 'YES'}

    def loader(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)

    def cache(self, **kwargs):
        kwargs.setdefault('min_interval', 10)
        return pbx_snapshot.SnapshotCache('dnd', self.loader, 120, clock=self.clock,
                                          utcnow=lambda: datetime(2026, 11, 6, 9, 0), **kwargs)

    def test_before_first_read_is_unknown(self):
        snap = self.cache().get()
        self.assertEqual((snap.state, snap.data, snap.updated_at), ('unknown', None, None))
        self.assertEqual(self.calls, 0)

    def test_reads_once_per_ttl(self):
        cache = self.cache()
        snap = cache.refresh()
        self.assertEqual((snap.state, snap.data, snap.updated_at), ('ok', {'201': 'YES'}, datetime(2026, 11, 6, 9, 0)))
        self.clock.now += 60
        cache.refresh()
        cache.get()
        self.assertEqual(self.calls, 1)
        self.clock.now += 61
        cache.refresh()
        self.assertEqual(self.calls, 2)

    def test_failure_keeps_old_data_and_waits_a_ttl(self):
        cache = self.cache()
        cache.refresh()
        self.clock.now += 120
        self.result = ConnectorError('unreachable', 'cannot connect', 'ami')
        snap = cache.refresh()
        self.assertEqual((snap.state, snap.data, snap.error.kind), ('stale', {'201': 'YES'}, 'unreachable'))
        self.clock.now += 30
        cache.refresh()
        self.assertEqual(self.calls, 2)  # not retried before the TTL is over

    def test_too_old_is_unknown_not_inactive(self):
        cache = self.cache()
        cache.refresh()
        self.result = ConnectorError('timeout', 'timed out', 'ami')
        for _ in range(3):
            self.clock.now += 121
            cache.refresh()
        snap = cache.get()
        self.assertEqual(snap.state, 'unknown')
        self.assertIsNone(snap.data)  # an old value must never be shown as if it were current

    def test_forced_refresh_respects_the_floor(self):
        cache = self.cache()
        cache.refresh()
        cache.refresh(force=True)
        self.assertEqual(self.calls, 1)
        self.clock.now += 11
        cache.refresh(force=True)
        self.assertEqual(self.calls, 2)

    def test_busy_lock_keeps_current_data(self):
        cache = self.cache()
        cache.refresh()
        self.clock.now += 200
        with pbx_snapshot._pbx_lock:  # another snapshot is talking to the PBX
            snap = cache.refresh()
        self.assertEqual((self.calls, snap.data), (1, {'201': 'YES'}))

    def test_loader_bug_is_contained(self):
        self.result = KeyError('boom')
        with self.assertLogs('hotel.pbx_snapshot', 'ERROR'):
            snap = self.cache().refresh()
        self.assertEqual((snap.state, snap.error.kind), ('unknown', 'protocol'))
        self.assertTrue(pbx_snapshot._pbx_lock.acquire(blocking=False))  # the lock was released
        pbx_snapshot._pbx_lock.release()

    def test_registry(self):
        self.assertEqual(pbx_snapshot.get('nothing').state, 'unknown')
        pbx_snapshot.register('dnd', self.loader, 120)
        pbx_snapshot.refresh_due()
        pbx_snapshot.refresh_due()
        self.assertEqual(self.calls, 1)
        self.assertEqual(pbx_snapshot.get('dnd').data, {'201': 'YES'})

    def test_snapshot_failure_does_not_touch_the_phone_sync_state(self):
        import app as m
        before = dict(m.pbx_sync_state)
        self.result = ConnectorError('unreachable', 'cannot connect', 'ami')
        pbx_snapshot.register('health', self.loader, 120)
        with self.assertLogs('hotel.pbx_snapshot', 'WARNING'):
            pbx_snapshot.refresh_due()
        self.assertEqual(m.pbx_sync_state, before)  # no PBX-LOST, no outage bookkeeping


# ---------------------------------------------------------------------------- tools
class CheckEnvTests(unittest.TestCase):
    def test_half_configured_groups_and_bad_choices(self):
        tool = load_tool('check_env')
        problems = tool.group_problems({'PBX_CDR_HOST': 'db', 'PBX_CDR_USER': 'r', 'PBX_AMI_HOST': 'x', 'PBX_AMI_USER': 'u',
                                        'PBX_AMI_SECRET': 's', 'PBX_SIP_DRIVER': 'iax', 'PBX_AMI_AUTH': 'md5'})
        self.assertEqual(len(problems), 2, problems)
        self.assertIn('PBX_CDR_PASS', problems[0])
        self.assertIn('PBX_SIP_DRIVER', problems[1])
        self.assertEqual(tool.group_problems({}), [])


class CheckPbxToolTests(AmiCase):
    def run_tool(self, env, *args):
        tool = load_tool('check_pbx')
        output = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(output):
            code = tool.main(list(args))
        return code, output.getvalue()

    def test_nothing_configured(self):
        code, out = self.run_tool({})
        self.assertEqual(code, 0)
        self.assertEqual(out.count('not configured'), 3)

    def test_ami_ok_and_cdr_failing(self):
        server = self.serve(astdb={('DND', '101'): 'YES'})
        env = {'PBX_AMI_HOST': '127.0.0.1', 'PBX_AMI_PORT': str(server.port), 'PBX_AMI_USER': server.username,
               'PBX_AMI_SECRET': SECRET, 'PBX_CDR_HOST': '127.0.0.1', 'PBX_CDR_PORT': '1', 'PBX_CDR_USER': 'r',
               'PBX_CDR_PASS': 'db-secret-pw', 'PBX_CDR_TIMEOUT': '1'}
        with mock.patch.object(pc, '_default_connect', side_effect=ConnectorError('unreachable', 'refused', 'cdr')):
            code, out = self.run_tool(env, '--dbget', '5')
        self.assertEqual(code, 1)
        self.assertIn('AMI        OK', out)
        self.assertIn('driver: sip (detected)', out)
        self.assertIn('5 reads', out)
        self.assertIn('CDR        FAILED (unreachable)', out)
        self.assertNotIn(SECRET, out)
        self.assertNotIn('db-secret-pw', out)


if __name__ == '__main__':
    unittest.main()
