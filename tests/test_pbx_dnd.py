"""DND / call forward (pbx_dnd.py, the /dnd page and the badge data in /api/extensions/status).

AstDB is served by the fake AMI server of test_pbx_connectors, so the whole path is real: snapshot
loader -> AmiReader (MD5 login, DBGet) -> pbx_snapshot cache -> page / API. Only the PBX is fake.
"""
import os
import unittest
from unittest import mock

import app as m
import features
import pbx_dnd
import pbx_snapshot
from pbx_snapshot import Snapshot
from tests.test_pbx_connectors import SECRET, AstServer
from tests.test_rbac import LEGACY_PASSWORD, AppCase

ASTDB = {('DND', '201'): 'YES', ('DND', '1005'): 'YES', ('CF', '305'): '09121234567', ('CFB', '100'): '101',
         ('CFU', '100'): ' 0912  7654321 ', ('DND', '999'): 'YES'}  # 999 is not in the directory


class FakeReader:
    def __init__(self, astdb):
        self.astdb, self.calls = astdb, []

    def db_get_many(self, family, keys):
        self.calls.append((family, list(keys)))
        return {k: self.astdb[(family, k)] for k in keys if (family, k) in self.astdb}


def snap(data, state='ok'):
    return Snapshot('dnd', data if state != 'unknown' else None, None, None, state)


class LogicTests(unittest.TestCase):
    def test_read_snapshot(self):
        reader = FakeReader({**ASTDB, ('CF', '201'): '   '})
        data = pbx_dnd.read_snapshot(reader, ['305', '201', '100', '201', '1005'])
        self.assertEqual(data['scanned'], ['100', '1005', '201', '305'])
        self.assertEqual(data['flags'], {'201': {'dnd': True}, '1005': {'dnd': True}, '305': {'cf': '09121234567'},
                                         '100': {'cfb': '101', 'cfu': '0912 7654321'}})
        self.assertEqual([family for family, _keys in reader.calls], ['DND', 'CF', 'CFB', 'CFU'])  # one pass per family

    def test_state_of(self):
        data = pbx_dnd.read_snapshot(FakeReader(ASTDB), ['201', '305', '100', '102'])
        self.assertEqual(pbx_dnd.state_of(snap(data), '201'), {'known': True, 'dnd': True, 'forwards': []})
        self.assertEqual(pbx_dnd.state_of(snap(data), '102'), {'known': True, 'dnd': False, 'forwards': []})
        self.assertEqual([f['kind'] for f in pbx_dnd.state_of(snap(data), '100')['forwards']], ['cfb', 'cfu'])
        # never "off" when we do not know
        self.assertFalse(pbx_dnd.state_of(snap(data), '777')['known'])           # not scanned
        self.assertEqual(pbx_dnd.state_of(snap(data, 'unknown'), '201'), {'known': False, 'dnd': None, 'forwards': []})
        self.assertFalse(pbx_dnd.state_of(None, '201')['known'])
        self.assertTrue(pbx_dnd.state_of(snap(data, 'stale'), '201')['dnd'])     # last good data, marked stale

    def test_flagged(self):
        data = pbx_dnd.read_snapshot(FakeReader(ASTDB), ['1005', '201', '305', '100'])
        dnd, forwards = pbx_dnd.flagged(snap(data))
        self.assertEqual(dnd, ['201', '1005'])
        self.assertEqual([(ext, f['kind'], f['to']) for ext, f in forwards],
                         [('100', 'cfb', '101'), ('100', 'cfu', '0912 7654321'), ('305', 'cf', '09121234567')])
        self.assertEqual(pbx_dnd.flagged(snap(None, 'unknown')), ([], []))


class DndPageCase(AppCase):
    def setUp(self):
        super().setUp()
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        self.server = AstServer(astdb=ASTDB, secret=SECRET)
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        env = mock.patch.dict(os.environ, {'PBX_AMI_HOST': '127.0.0.1', 'PBX_AMI_PORT': str(self.server.port),
                                           'PBX_AMI_USER': self.server.username, 'PBX_AMI_SECRET': SECRET, 'PBX_AMI_TIMEOUT': '2'})
        env.start()
        self.addCleanup(env.stop)
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            for ext, title, kind in (('201', 'Room 201', 'room'), ('1005', 'Room 1005', 'room'), ('305', 'Room 305', 'room'),
                                     ('100', 'Reception', 'staff'), ('101', 'Reception 2', 'staff'), ('102', 'Office', 'staff')):
                m.db.session.add(m.Extension(extension=ext, title=title, kind=kind))
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

    def refresh(self):
        return pbx_snapshot.refresh(pbx_dnd.SNAPSHOT_NAME, force=True)


class DndPageTests(DndPageCase):
    def test_snapshot_reads_every_directory_extension(self):
        result = self.refresh()
        self.assertEqual(result.state, 'ok')
        self.assertEqual(result.data['scanned'], ['100', '1005', '101', '102', '201', '305'])
        self.assertNotIn('999', result.data['flags'])  # not in the directory, not asked
        self.assertNotIn(SECRET, str(self.server.seen))

    def test_page_lists_dnd_and_forwards(self):
        self.refresh()
        page = self.client().get(f'/{self.prefix}/dnd').get_data(as_text=True)
        for text in ('Room 201', 'Room 1005', '09121234567', 'هنگام اشغال', '(Reception 2)', 'آخرین خواندن از ایزابل'):
            self.assertIn(text, page)

    def test_filters(self):
        self.refresh()
        client = self.client()
        page = client.get(f'/{self.prefix}/dnd?floor=10').get_data(as_text=True)
        self.assertIn('Room 1005', page)
        self.assertNotIn('Room 201', page)
        page = client.get(f'/{self.prefix}/dnd?kind=staff').get_data(as_text=True)
        self.assertIn('Reception', page)
        self.assertNotIn('Room 305', page)

    def test_pages_never_ask_the_pbx(self):
        self.refresh()
        before = len(self.server.seen)
        client = self.client()
        for _ in range(3):
            client.get(f'/{self.prefix}/dnd')
            client.get('/api/extensions/status')
        self.assertEqual(len(self.server.seen), before)

    def test_refresh_button_is_rate_limited(self):
        client = self.client()
        client.post(f'/{self.prefix}/dnd/refresh')
        logins = sum(1 for a in self.server.seen if a['Action'] == 'Login')
        client.post(f'/{self.prefix}/dnd/refresh')
        self.assertEqual(sum(1 for a in self.server.seen if a['Action'] == 'Login'), logins)  # second click within 30 s
        self.assertEqual(logins, 1)

    def test_unknown_before_first_read(self):
        page = self.client().get(f'/{self.prefix}/dnd').get_data(as_text=True)
        self.assertIn('نامشخص', page)
        self.assertIn('هنوز از ایزابل خوانده نشده است', page)
        flags = self.client().get('/api/extensions/status').get_json()['flags']
        self.assertEqual((flags['state'], flags['items'], flags['scanned']), ('unknown', {}, []))

    def test_pbx_down_keeps_last_data_marked_stale(self):
        self.refresh()
        self.server.shutdown()
        self.server.server_close()
        cache = pbx_snapshot._caches[pbx_dnd.SNAPSHOT_NAME]
        cache._attempt_mono -= 1000  # the TTL and the 30 s floor have passed
        with self.assertLogs('hotel.pbx_snapshot', 'WARNING'):
            result = cache.refresh()
        self.assertEqual(result.state, 'stale')
        page = self.client().get(f'/{self.prefix}/dnd').get_data(as_text=True)
        self.assertIn('Room 201', page)                     # last good data
        self.assertIn('آخرین تلاش ناموفق بود', page)
        self.assertIn('در دسترس نیست', page)                # the Persian reason
        self.assertIsNone(m.pbx_sync_state['error_since'])  # no PBX-LOST for the phones

    def test_status_api_carries_badges_for_allowed_users_only(self):
        self.refresh()
        flags = self.client('op').get('/api/extensions/status').get_json()['flags']
        self.assertEqual(flags['state'], 'ok')
        self.assertTrue(flags['items']['201']['dnd'])
        self.assertEqual(flags['items']['305']['forwards'], [{'kind': 'cf', 'label': 'همیشه', 'to': '09121234567'}])
        self.assertIn('102', flags['scanned'])  # scanned and absent from items = known to be off
        self.assertNotIn('flags', self.client('view').get('/api/extensions/status').get_json())
        self.assertNotIn('flags', self.app.test_client().get('/api/extensions/status').get_json())  # public

    def test_detail_modal_rows_only_with_permission(self):
        self.assertIn('data-field="dnd"', self.client('op').get('/').get_data(as_text=True))
        self.assertNotIn('data-field="dnd"', self.client('view').get('/').get_data(as_text=True))


class DndWithoutAmiTests(AppCase):
    def test_page_explains_ami_is_needed(self):
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        self.start()
        self.assertNotIn(pbx_dnd.SNAPSHOT_NAME, pbx_snapshot._caches)
        with self.app.app_context():
            role = m.Role.query.filter_by(key=features.SUPER_ADMIN).one()
            user = m.User(username='boss', display_name='boss', role_id=role.id)
            user.set_password(LEGACY_PASSWORD)
            m.db.session.add(user)
            m.db.session.commit()
        client = self.app.test_client()
        client.post(f"/{self.app.config['ADMIN_URL_PREFIX']}/login", data={'username': 'boss', 'password': LEGACY_PASSWORD})
        page = client.get(f"/{self.app.config['ADMIN_URL_PREFIX']}/dnd").get_data(as_text=True)
        self.assertIn('اتصال AMI', page)


if __name__ == '__main__':
    unittest.main()
