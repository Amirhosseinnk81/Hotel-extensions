"""Day / night mode (pbx_daynight.py, AmiWriter and the /daynight pages).

Writes go to the fake AMI server (Setvar / DBPut change its AstDB like Asterisk would), so the whole path
is exercised: confirm page -> fresh read -> one write -> read back -> activity log. Checked on top:
the white list, the kill switch, refusal when the state changed meanwhile (double click), no retry,
read-back mismatch, BLF failure, permissions. The real mechanism on IssabelPBX 2.11 is an assumption to
confirm on the PBX (DEPLOYMENT.md 11-11); tools/check_pbx.py --test-daynight-write is the safe first step.
"""
import contextlib
import io
import os
import unittest
from unittest import mock

import app as m
import features
import pbx_connectors
import pbx_daynight as dn
import pbx_snapshot
from tests.test_pbx_connectors import SECRET, AstServer, load_tool
from tests.test_rbac import LEGACY_PASSWORD, AppCase

ROWS = [{'ext': '0', 'dmode': 'fc_description', 'dest': 'پذیرش'}, {'ext': '0', 'dmode': 'day', 'dest': 'ext-group,600,1'},
        {'ext': '0', 'dmode': 'night', 'dest': 'ivr-2,s,1'}, {'ext': '1', 'dmode': 'fc_description', 'dest': 'رستوران'},
        {'ext': '1', 'dmode': 'day', 'dest': 'from-did-direct,301,1'}, {'ext': '1', 'dmode': 'night', 'dest': 'app-blackhole,hangup,1'},
        {'ext': '12', 'dmode': 'day', 'dest': 'x'}, {'ext': 'abc', 'dmode': 'day', 'dest': 'x'}]


class LogicTests(unittest.TestCase):
    def test_items_and_destinations(self):
        items = dn.parse_items(ROWS)
        self.assertEqual(sorted(items), [0, 1])
        self.assertEqual(items[0], {'name': 'پذیرش', 'day': 'ext-group,600,1', 'night': 'ivr-2,s,1'})
        self.assertEqual([dn.describe_destination(d) for d in ('ext-group,600,1', 'ivr-2,s,1', 'from-did-direct,301,1',
                                                              'app-blackhole,hangup,1', 'ext-local,vmu101,1', 'odd,1,1', None)],
                         ['گروه زنگ 600', 'منوی صوتی (IVR) 2', 'داخلی 301', 'قطع تماس', 'صندوق صوتی 101', 'odd,1,1', ''])

    def test_parsing_settings(self):
        self.assertEqual(dn.parse_whitelist('0, 1,9,10,x,-1'), {0, 1, 9})
        self.assertEqual(dn.parse_labels('0:پذیرش,x:y,11:z,1:'), {0: 'پذیرش'})
        self.assertEqual(dn.safe_table('hotelapp_ro.daynight'), 'hotelapp_ro.daynight')
        self.assertEqual(dn.safe_table('asterisk.daynight; DROP TABLE x'), 'asterisk.daynight')
        self.assertEqual([dn.state_of(v) for v in ('NIGHT', 'night ', 'DAY', '', None, 'x')], ['NIGHT', 'NIGHT', 'DAY', 'DAY', 'DAY', 'DAY'])
        self.assertNotIn('password', dn.ITEMS_SQL.format(table='asterisk.daynight'))

    def test_write_actions(self):
        self.assertEqual(dn.write_actions(0, 'NIGHT'), [('Setvar', {'Variable': 'DB(DAYNIGHT/C0)', 'Value': 'NIGHT'}),
                                                        ('Setvar', {'Variable': 'DEVICE_STATE(Custom:DAYNIGHT0)', 'Value': 'INUSE'})])
        self.assertEqual(dn.write_actions(3, 'DAY', 'dbput', devstate=False), [('DBPut', {'Family': 'DAYNIGHT', 'Key': 'C3', 'Val': 'DAY'})])
        for bad in ((10, 'DAY'), (-1, 'DAY'), ('0', 'DAY'), (0, 'night'), (0, 'NIGHT\r\nAction: Originate')):
            with self.assertRaises(ValueError):
                dn.write_actions(*bad)
        with self.assertRaises(ValueError):
            dn.write_actions(0, 'DAY', 'originate')

    def test_switch_flow(self):
        store, writes = {0: 'DAY'}, []

        def read(i):
            return store[i]

        def write(i, state):
            writes.append((i, state))
            store[i] = state
        self.assertEqual(dn.switch(read, write, 0, 'DAY', 'NIGHT'), 'NIGHT')
        with self.assertRaises(dn.SwitchError) as ctx:  # second click: already night
            dn.switch(read, write, 0, 'DAY', 'NIGHT')
        self.assertFalse(ctx.exception.written)
        self.assertEqual(writes, [(0, 'NIGHT')])
        with self.assertRaises(dn.SwitchError) as ctx:  # accepted by the PBX but nothing changed
            dn.switch(read, lambda i, s: writes.append('ignored'), 0, 'NIGHT', 'DAY')
        self.assertTrue(ctx.exception.written)
        with self.assertRaises(dn.SwitchError):
            dn.switch(read, write, 0, 'NIGHT', 'NIGHT')


# ---------------------------------------------------------------------------- pages
class FakeCdr:
    def __init__(self):
        self.queries = []

    def select(self, sql, params=(), max_rows=1000):
        self.queries.append(sql)
        if 'daynight' in sql:
            return [dict(r) for r in ROWS]
        return [{'timeconditions_id': 1, 'displayname': 'ساعت کاری'}]


class DayNightCase(AppCase):
    EXTRA = {}

    def setUp(self):
        super().setUp()
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        self.server = AstServer(secret=SECRET, astdb={('DAYNIGHT', 'C0'): 'DAY', ('TC', '1'): 'false_sticky'})
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        env = {'PBX_AMI_HOST': '127.0.0.1', 'PBX_AMI_PORT': str(self.server.port), 'PBX_AMI_USER': self.server.username,
               'PBX_AMI_SECRET': SECRET, 'PBX_AMI_TIMEOUT': '2', 'PBX_AMI_WRITE_USER': self.server.username,
               'PBX_AMI_WRITE_SECRET': SECRET, 'PBX_DAYNIGHT_ALLOWED': '0', 'PBX_DAYNIGHT_WRITE_ENABLED': '1',
               'PBX_CDR_HOST': '10.0.0.2', 'PBX_CDR_USER': 'r', 'PBX_CDR_PASS': 'p', **self.EXTRA}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cdr = FakeCdr()
        cdr_patch = mock.patch.object(m, 'make_cdr_client', lambda: self.cdr)
        cdr_patch.start()
        self.addCleanup(cdr_patch.stop)
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            roles = {r.key: r.id for r in m.Role.query.all()}
            for name, role in (('boss', features.SUPER_ADMIN), ('op', 'operator'), ('view', 'viewer')):
                user = m.User(username=name, display_name=name, role_id=roles[role])
                user.set_password(LEGACY_PASSWORD)
                m.db.session.add(user)
            m.db.session.commit()
        pbx_snapshot.refresh(dn.SNAPSHOT_NAME, force=True)

    def client(self, name='boss'):
        client = self.app.test_client()
        client.post(f'/{self.prefix}/login', data={'username': name, 'password': LEGACY_PASSWORD})
        return client

    def url(self, path=''):
        return f'/{self.prefix}/daynight{path}'

    def switch(self, client, index=0, expected='DAY', target='NIGHT'):
        return client.post(self.url(f'/{index}/switch'), data={'expected': expected, 'target': target},
                           environ_base={'REMOTE_ADDR': '10.1.2.3'})

    def writes(self):
        return [a for a in self.server.seen if a['Action'] in ('Setvar', 'DBPut')]

    def logs(self):
        with self.app.app_context():
            return [(l.action, l.actor, l.ip, l.entity_label, l.details)
                    for l in m.ActivityLog.query.filter_by(entity_type='daynight').order_by(m.ActivityLog.id).all()]


class ViewTests(DayNightCase):
    def test_page_shows_names_states_and_destinations(self):
        page = self.client('op').get(self.url()).get_data(as_text=True)
        for text in ('پذیرش', 'رستوران', 'گروه زنگ 600', 'منوی صوتی (IVR) 2', 'قطع تماس', 'ساعت کاری', 'false_sticky'):
            self.assertIn(text, page)
        self.assertNotIn('/confirm', page)  # the operator may look, not switch
        self.assertTrue(all('password' not in q for q in self.cdr.queries))

    def test_operator_and_viewer_cannot_switch(self):
        client = self.client('op')
        self.assertEqual(client.get(self.url('/0/confirm')).status_code, 403)
        self.assertEqual(self.switch(client).status_code, 403)
        self.assertEqual(self.client('view').get(self.url()).status_code, 403)
        self.assertEqual(self.writes(), [])

    def test_confirm_page_names_both_destinations(self):
        page = self.client().get(self.url('/0/confirm')).get_data(as_text=True)
        self.assertIn('گروه زنگ 600', page)
        self.assertIn('منوی صوتی (IVR) 2', page)
        self.assertIn('name="expected" value="DAY"', page)
        self.assertIn('name="target" value="NIGHT"', page)
        self.assertEqual(self.writes(), [])  # confirming writes nothing


class SwitchTests(DayNightCase):
    def test_switch_writes_once_reads_back_and_logs(self):
        client = self.client()
        response = self.switch(client)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.server.astdb[('DAYNIGHT', 'C0')], 'NIGHT')
        self.assertEqual(self.server.devstates, {'DAYNIGHT0': 'INUSE'})
        self.assertEqual([w['Variable'] for w in self.writes()], ['DB(DAYNIGHT/C0)', 'DEVICE_STATE(Custom:DAYNIGHT0)'])
        action, actor, ip, label, details = self.logs()[0]
        self.assertEqual((action, actor, ip, label), ('switched', 'boss', '10.1.2.3', 'C0 — پذیرش'))
        self.assertIn('روز → شب', details)
        page = client.get(self.url()).get_data(as_text=True)
        self.assertIn('تأیید شد', page)       # the flash
        self.assertIn('boss', page)           # "last change by"
        self.assertIn('تعویض به روز', page)   # the button now offers the way back

    def test_double_click_is_refused_without_a_second_write(self):
        client = self.client()
        self.switch(client)
        self.switch(client)  # the same form posted again
        self.assertEqual(len(self.writes()), 2)  # only the first switch wrote (state + BLF)
        self.assertEqual([l[0] for l in self.logs()], ['switched', 'switch_failed'])
        self.assertIn('عوض شده است', client.get(self.url()).get_data(as_text=True))

    def test_switch_back(self):
        client = self.client()
        self.switch(client)
        self.switch(client, expected='NIGHT', target='DAY')
        self.assertEqual(self.server.astdb[('DAYNIGHT', 'C0')], 'DAY')
        self.assertEqual(self.server.devstates['DAYNIGHT0'], 'NOT_INUSE')

    def test_white_list_and_kill_switch(self):
        client = self.client()
        self.switch(client, index=1)  # not in PBX_DAYNIGHT_ALLOWED
        with mock.patch.dict(os.environ, {'PBX_DAYNIGHT_WRITE_ENABLED': '0'}):
            self.switch(client)
            self.assertEqual(client.get(self.url('/0/confirm')).status_code, 302)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.server.astdb[('DAYNIGHT', 'C0')], 'DAY')

    def test_busy_lock(self):
        with m.daynight_lock:
            self.switch(self.client())
        self.assertEqual(self.writes(), [])

    def test_write_accepted_but_not_effective(self):
        self.server.ignore_writes = True
        self.switch(self.client())
        self.assertEqual(self.logs()[0][0], 'switch_failed')
        self.assertIn('نوشته شد ولی تأیید نشد', self.logs()[0][4])

    def test_refused_write_is_not_retried(self):
        self.server.denied.add('Setvar')
        self.switch(self.client())
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.server.astdb[('DAYNIGHT', 'C0')], 'DAY')
        self.assertIn('انجام نشد', self.logs()[0][4])

    def test_blf_failure_is_reported_but_the_switch_stands(self):
        self.server.fail_devstate = True
        client = self.client()
        self.switch(client)
        self.assertEqual(self.server.astdb[('DAYNIGHT', 'C0')], 'NIGHT')
        self.assertEqual(self.logs()[0][0], 'switched')
        self.assertIn('BLF', client.get(self.url()).get_data(as_text=True))


class DbPutTests(DayNightCase):
    EXTRA = {'PBX_DAYNIGHT_WRITE_METHOD': 'dbput', 'PBX_DAYNIGHT_DEVSTATE': '0'}

    def test_dbput_method(self):
        self.switch(self.client())
        self.assertEqual([w['Action'] for w in self.writes()], ['DBPut'])
        self.assertEqual(self.server.astdb[('DAYNIGHT', 'C0')], 'NIGHT')


class WriteUserTests(DayNightCase):
    EXTRA = {'PBX_AMI_WRITE_USER': ''}

    def test_no_write_user_no_switch(self):
        self.switch(self.client())
        self.assertEqual(self.writes(), [])
        self.assertIn('PBX_AMI_WRITE_USER', self.client().get(self.url()).get_data(as_text=True))


class SafeTestToolTests(DayNightCase):
    def test_writes_only_the_test_key(self):
        tool = load_tool('check_pbx')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tool.main(['--test-daynight-write'])
        self.assertIn('Write test OK', out.getvalue())
        self.assertEqual(code, 1)  # the CDR check in the same run fails (no real MySQL); the write test passed
        self.assertTrue(self.server.astdb[('HOTELTEST', 'probe')].startswith('T'))
        self.assertEqual(self.server.astdb[('DAYNIGHT', 'C0')], 'DAY')
        self.assertTrue(all(w['Variable'].startswith('DB(HOTELTEST/') for w in self.writes()))
        self.assertNotIn(SECRET, out.getvalue())


if __name__ == '__main__':
    unittest.main()
