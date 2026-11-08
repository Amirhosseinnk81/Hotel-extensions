"""Directory vs. Issabel comparison (pbx_directory_sync.py, the /pbx-directory pages) and PBX_AUTO_CREATE.

The PBX list comes from a fake MySQL client (asterisk.users + devices rows) or from the fake AMI server;
nothing here can create, change or delete anything on the PBX, and the tests check that the only
writes go to the app's own directory.
"""
import os
import unittest
from unittest import mock

import app as m
import features
import pbx_directory_sync as pds
import pbx_snapshot
from tests.test_pbx_connectors import SECRET, AstServer
from tests.test_pbx_sync import SyncCase
from tests.test_rbac import LEGACY_PASSWORD, AppCase


def kind_of(ext):
    return 'room' if ext.startswith('2') and len(ext) == 3 else 'staff'


BOOK = [
    {'extension': '100', 'title': 'پذیرش', 'person_name': None, 'kind': 'staff', 'is_deleted': False, 'is_active': True},
    {'extension': '101', 'title': 'Front Desk', 'person_name': 'Sara Ahmadi', 'kind': 'staff', 'is_deleted': False, 'is_active': True},
    {'extension': '120', 'title': 'Old office', 'person_name': None, 'kind': 'staff', 'is_deleted': False, 'is_active': False},
    {'extension': '201', 'title': 'اتاق 201', 'person_name': None, 'kind': 'staff', 'is_deleted': False, 'is_active': True},
    {'extension': '202', 'title': 'Room 202', 'person_name': None, 'kind': 'room', 'is_deleted': True, 'is_active': True},
    {'extension': '777', 'title': 'داخلی 777', 'person_name': None, 'kind': 'pending', 'is_deleted': False, 'is_active': True},
]
PBX = {'100': {'name': 'پذيرش', 'tech': 'sip'},            # Arabic ي: the same name
       '101': {'name': 'sara  ahmadi', 'tech': 'sip'},     # the person, other case / spacing
       '201': {'name': 'Room 201', 'tech': 'sip'},          # really different
       '202': {'name': 'Room 202', 'tech': 'sip'},          # in the recycle bin
       '203': {'name': None, 'tech': 'sip'},
       '777': {'name': None, 'tech': 'pjsip'},
       '150': {'name': 'Laundry', 'tech': 'sip'}}


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.result = pds.compare(PBX, BOOK, kind_of)

    def test_missing_in_book(self):
        self.assertEqual([(i['extension'], i['name'], i['suggested_kind']) for i in self.result['missing_in_book']],
                         [('150', 'Laundry', 'staff'), ('203', None, 'room')])

    def test_trash_is_separate(self):
        self.assertEqual([i['extension'] for i in self.result['in_trash']], ['202'])
        self.assertNotIn('202', [i['extension'] for i in self.result['missing_in_book']])

    def test_missing_in_pbx_includes_inactive(self):
        self.assertEqual([(i['extension'], i['is_active']) for i in self.result['missing_in_pbx']], [('120', False)])

    def test_names(self):
        self.assertEqual([(i['extension'], i['name']) for i in self.result['name_mismatch']], [('201', 'Room 201')])
        self.assertEqual(pds.normalize_name(' Front-Desk '), pds.normalize_name('front desk'))
        self.assertEqual(pds.normalize_name('كيان‌پور'), pds.normalize_name('کیان پور'))

    def test_kinds(self):
        self.assertEqual([(i['extension'], i['kind'], i['suggested_kind']) for i in self.result['kind_mismatch']], [('201', 'staff', 'room')])
        self.assertEqual([i['extension'] for i in self.result['pending']], ['777'])
        self.assertEqual(pds.counts(self.result)['missing_in_book'], 2)

    def test_default_title(self):
        self.assertEqual(pds.default_title('203', None, 'room'), 'اتاق 203')
        self.assertEqual(pds.default_title('150', None, 'staff'), 'داخلی 150')
        self.assertEqual(pds.default_title('150', 'Laundry', 'staff'), 'Laundry')


class FakeCdr:
    def __init__(self, rows=None, fail=None):
        self.rows, self.fail, self.queries = rows, fail, []

    def select(self, sql, params=(), max_rows=1000):
        self.queries.append(sql)
        if self.fail:
            raise self.fail
        return list(self.rows if self.rows is not None else
                    [{'extension': ext, 'name': info['name'], 'tech': info['tech']} for ext, info in PBX.items()])


class ReadTests(unittest.TestCase):
    def test_mysql_source(self):
        cdr = FakeCdr(rows=[{'extension': '101', 'name': '  Front   Desk ', 'tech': 'sip'}, {'extension': 'trunk-a', 'name': 'x', 'tech': 'sip'},
                            {'extension': '102', 'name': '', 'tech': None}])
        data = pds.read_pbx_extensions(cdr_client=cdr)
        self.assertEqual(data, {'source': 'mysql', 'extensions': {'101': {'name': 'Front Desk', 'tech': 'sip'}, '102': {'name': None, 'tech': None}}})
        self.assertTrue(cdr.queries[0].startswith('SELECT') and 'asterisk.sip' not in cdr.queries[0])  # never the table with secrets

    def test_ami_source(self):
        server = AstServer(secret=SECRET)
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        from pbx_connectors import AmiReader, AmiSettings, CdrSettings, Settings, forget_detected_driver
        forget_detected_driver()
        self.addCleanup(forget_detected_driver)
        settings = Settings(ami=AmiSettings(host='127.0.0.1', port=server.port, user=server.username, secret=SECRET, timeout=2), cdr=CdrSettings())
        with AmiReader(settings) as reader:
            data = pds.read_pbx_extensions(ami_reader=reader)
        self.assertEqual(data['source'], 'ami')
        self.assertIn('1001', data['extensions'])
        self.assertNotIn('main-trunk', data['extensions'])
        self.assertEqual(data['extensions']['100'], {'name': None, 'tech': 'sip'})


# ---------------------------------------------------------------------------- pages
class DirectoryPageCase(AppCase):
    def setUp(self):
        super().setUp()
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        env = mock.patch.dict(os.environ, {'PBX_CDR_HOST': '10.0.0.2', 'PBX_CDR_USER': 'hotel_cdr', 'PBX_CDR_PASS': 'db-test-pw'})
        env.start()
        self.addCleanup(env.stop)
        self.cdr = FakeCdr()
        patcher = mock.patch.object(m, 'make_cdr_client', lambda: self.cdr)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            for e in BOOK:
                m.db.session.add(m.Extension(**e))
            roles = {r.key: r.id for r in m.Role.query.all()}
            for name, role in (('boss', features.SUPER_ADMIN), ('op', 'operator'), ('view', 'viewer')):
                user = m.User(username=name, display_name=name, role_id=roles[role])
                user.set_password(LEGACY_PASSWORD)
                m.db.session.add(user)
            m.db.session.commit()

    def client(self, name='boss'):
        client = self.app.test_client()
        client.post(f'/{self.prefix}/login', data={'username': name, 'password': LEGACY_PASSWORD})
        return client

    def url(self, path=''):
        return f'/{self.prefix}/pbx-directory{path}'

    def ext(self, number):
        with self.app.app_context():
            return m.Extension.query.filter_by(extension=number).first()


class DirectoryPageTests(DirectoryPageCase):
    def test_before_first_read(self):
        page = self.client().get(self.url()).get_data(as_text=True)
        self.assertIn('هنوز از ایزابل خوانده نشده است', page)
        self.assertEqual(self.cdr.queries, [])  # opening the page never asks the PBX

    def test_refresh_shows_report_and_is_logged(self):
        client = self.client('op')
        client.post(self.url('/refresh'))
        page = client.get(self.url()).get_data(as_text=True)
        for text in ('Laundry', 'Room 201', 'Old office', 'MySQL'):
            self.assertIn(text, page)
        self.assertNotIn('name="ext"', page)  # the operator may not add extensions
        with self.app.app_context():
            log = m.ActivityLog.query.filter_by(entity_type='pbxsync').one()
        self.assertEqual(log.action, 'compared')
        self.assertIn('نیست در دفترچه: 2', log.details)
        client.post(self.url('/refresh'))  # within 30 s: not read again, not logged again
        self.assertEqual(len(self.cdr.queries), 1)

    def test_add_only_to_the_directory(self):
        client = self.client()
        client.post(self.url('/refresh'))
        response = client.post(self.url('/add'), data={'ext': ['150', '203'], 'kind_203': 'room', 'kind_150': 'room'})
        self.assertEqual(response.status_code, 302)
        laundry, room = self.ext('150'), self.ext('203')
        self.assertEqual((laundry.title, laundry.kind, laundry.is_active), ('Laundry', 'room', True))
        self.assertEqual((room.title, room.kind), ('اتاق 203', 'room'))
        with self.app.app_context():
            logs = m.ActivityLog.query.filter_by(entity_type='extension', action='created').all()
        self.assertEqual(len(logs), 2)
        self.assertTrue(all('مقایسه با ایزابل' in log.details for log in logs))
        self.assertEqual(len(self.cdr.queries), 1)  # adding reads nothing more from the PBX, writes nothing to it
        self.assertTrue(all(q.startswith('SELECT') for q in self.cdr.queries))

    def test_add_rechecks_the_server_side_list(self):
        client = self.client()
        client.post(self.url('/refresh'))
        client.post(self.url('/add'), data={'ext': ['101', '202', '999', "203' OR 1=1", '203', '203'], 'kind_203': 'admin'})
        self.assertEqual(self.ext('101').title, 'Front Desk')   # existing entry untouched
        self.assertTrue(self.ext('202').is_deleted)              # in the bin: not re-created
        self.assertIsNone(self.ext('999'))                       # not on Issabel
        self.assertEqual(self.ext('203').kind, 'room')           # bad kind -> the suggestion
        with self.app.app_context():
            self.assertEqual(m.Extension.query.filter_by(extension='203').count(), 1)

    def test_permissions(self):
        self.assertEqual(self.client('op').post(self.url('/add'), data={'ext': ['150']}).status_code, 403)
        self.assertEqual(self.client('view').get(self.url()).status_code, 403)
        with self.app.app_context():  # extensions.create without pbxsync.view is not enough either
            role = m.Role(key='creator', name='creator', permissions=[m.RolePermission(permission_key='extensions.create')])
            m.db.session.add(role)
            m.db.session.flush()
            user = m.User(username='creator', display_name='creator', role_id=role.id)
            user.set_password(LEGACY_PASSWORD)
            m.db.session.add(user)
            m.db.session.commit()
        self.assertEqual(self.client('creator').post(self.url('/add'), data={'ext': ['150']}).status_code, 403)
        self.assertIsNone(self.ext('150'))

    def test_error_is_shown_in_persian(self):
        from pbx_connectors import ConnectorError
        self.cdr.fail = ConnectorError('permission', 'SELECT command denied to user for table users', 'cdr')
        client = self.client()
        with self.assertLogs('hotel.pbx_snapshot', 'WARNING'):
            client.post(self.url('/refresh'))
        page = client.get(self.url()).get_data(as_text=True)
        self.assertIn('دسترسی ندارد', page)
        with self.app.app_context():
            self.assertEqual(m.ActivityLog.query.filter_by(entity_type='pbxsync').count(), 0)


class NotConfiguredTests(AppCase):
    def test_page_explains(self):
        pbx_snapshot.clear()
        self.addCleanup(pbx_snapshot.clear)
        self.start()
        self.assertNotIn(pds.SNAPSHOT_NAME, pbx_snapshot.names())
        with self.app.app_context():
            role = m.Role.query.filter_by(key=features.SUPER_ADMIN).one()
            user = m.User(username='boss', display_name='boss', role_id=role.id)
            user.set_password(LEGACY_PASSWORD)
            m.db.session.add(user)
            m.db.session.commit()
        client = self.app.test_client()
        prefix = self.app.config['ADMIN_URL_PREFIX']
        client.post(f'/{prefix}/login', data={'username': 'boss', 'password': LEGACY_PASSWORD})
        self.assertIn('MySQL فقط‌خواندنی', client.get(f'/{prefix}/pbx-directory').get_data(as_text=True))
        response = client.post(f'/{prefix}/pbx-directory/refresh')
        self.assertEqual(response.status_code, 302)


class AutoCreateSwitchTests(SyncCase):
    def test_default_keeps_creating(self):
        self.assertEqual(self.sync()['created'], 6)

    def test_off_creates_nothing(self):
        with mock.patch.dict(os.environ, {'PBX_AUTO_CREATE': '0'}):
            summary = self.sync()
        self.assertEqual((summary['created'], summary['not_in_book']), (0, 6))
        self.assertIsNone(self.get_ext('1001'))
        self.assertEqual(self.get_ext('100')['reg_status'], 'online')  # known extensions still get their live state


if __name__ == '__main__':
    unittest.main()
