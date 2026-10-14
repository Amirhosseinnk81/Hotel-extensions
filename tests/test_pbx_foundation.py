"""Tests for the guest-room / staff split, MAC handling and the MAC import wizard.

Run from the project folder:  python -m unittest discover -s tests -t .
Every test gets its own temporary database; nothing touches hotel_extensions.db.
"""
import csv
import io
import logging
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from unittest import mock

from openpyxl import Workbook

import app as m

ADMIN_PASSWORD = 'test-pass-123'


class AppTestCase(unittest.TestCase):
    """Fresh app + temp dir per test, with an admin client and an anonymous client."""

    def make_app(self, prepare_db=None):
        self.tmp = tempfile.mkdtemp()
        saved = (m.BASE_DIR, m.IMPORT_TMP_DIR, m.SECRET_KEY_FILE, m.ADMIN_PREFIX_FILE)
        m.BASE_DIR = self.tmp
        m.IMPORT_TMP_DIR = os.path.join(self.tmp, 'tmp_imports')
        m.SECRET_KEY_FILE = os.path.join(self.tmp, '.secret_key')
        m.ADMIN_PREFIX_FILE = os.path.join(self.tmp, '.admin_prefix')

        def restore():
            m.BASE_DIR, m.IMPORT_TMP_DIR, m.SECRET_KEY_FILE, m.ADMIN_PREFIX_FILE = saved
            shutil.rmtree(self.tmp, ignore_errors=True)

        self.addCleanup(restore)
        if prepare_db:
            prepare_db(os.path.join(self.tmp, 'hotel_extensions.db'))
        m.bump_data_version()
        self.app = m.create_app()
        for handler in list(self.app.logger.handlers):
            if isinstance(handler, RotatingFileHandler):
                self.app.logger.removeHandler(handler)
                handler.close()
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        self.anon = self.app.test_client()
        self.admin = self.app.test_client()
        with self.app.app_context():
            user = m.User.query.filter_by(username='admin').first()
            user.set_password(ADMIN_PASSWORD)
            user.must_change_password = False
            m.db.session.commit()
        self.admin.post(f'/{self.prefix}/login', data={'username': 'admin', 'password': ADMIN_PASSWORD})

    def setUp(self):
        self.make_app()

    def add(self, extension, title, **fields):
        with self.app.app_context():
            fields.setdefault('kind', 'staff')
            m.db.session.add(m.Extension(extension=extension, title=title, **fields))
            m.db.session.commit()
        m.bump_data_version()

    def get_ext(self, extension):
        with self.app.app_context():
            entry = m.Extension.query.filter_by(extension=extension).first()
            if entry is None:
                return None
            return {c.name: getattr(entry, c.name) for c in m.Extension.__table__.columns}

    def url(self, path):
        return f'/{self.prefix}{path}'


class RoomRuleTests(unittest.TestCase):
    def test_guest_room_numbers(self):
        for number in ('201', '227', '301', '927', '1001', '1027', '1101', '2301', '2327'):
            self.assertTrue(m.is_room_number(number), number)

    def test_office_numbers_are_not_rooms(self):
        for number in ('0', '100', '117', '199', '228', '250', '333', '350', '444', '1000', '2328', '2401', '2424', '2525', 'abc', ''):
            self.assertFalse(m.is_room_number(number), number)

    def test_known_collision_is_documented(self):
        # 410 / 411 are real floor-4 offices that also match the room pattern; this is why
        # existing records keep their kind and the admin can always override it.
        self.assertTrue(m.is_room_number('410'))
        self.assertEqual(m.suggest_kind('410'), 'room')
        self.assertEqual(m.suggest_kind('120'), 'staff')

    def test_parse_kind(self):
        self.assertEqual(m.parse_kind('پرسنل'), 'staff')
        self.assertEqual(m.parse_kind('Room'), 'room')
        self.assertEqual(m.parse_kind('اتاق مهمان'), 'room')
        self.assertEqual(m.parse_kind('در انتظار بررسی'), 'pending')
        self.assertIsNone(m.parse_kind('whatever'))


class MacNormalizeTests(unittest.TestCase):
    def test_valid_formats(self):
        expected = '00:15:65:AA:BB:CC'
        for value in ('00:15:65:aa:bb:cc', '00-15-65-AA-BB-CC', '001565aabbcc', '0015.65aa.bbcc', ' 00 15 65 AA BB CC ', '00:15:65:AA:BB:CC'):
            self.assertEqual(m.normalize_mac(value), expected, value)

    def test_invalid(self):
        for value in (None, '', '   ', '00:15:65:AA:BB', 'GG:15:65:AA:BB:CC', '00:15:65:AA:BB:CC:DD', '000000000000', 'FF:FF:FF:FF:FF:FF', 1565123456.0, 'abc'):
            self.assertIsNone(m.normalize_mac(value), value)


class StatusDisplayTests(unittest.TestCase):
    def test_states(self):
        entry = m.Extension(extension='1', title='t')
        self.assertIsNone(m.display_phone_status(entry))
        entry.reg_status, entry.status_updated_at = 'online', datetime.utcnow()
        self.assertEqual(m.display_phone_status(entry), 'online')
        entry.reg_status = 'offline'
        self.assertEqual(m.display_phone_status(entry), 'offline')
        entry.status_updated_at = datetime.utcnow() - timedelta(minutes=m.PBX_STATUS_STALE_MINUTES + 1)
        self.assertEqual(m.display_phone_status(entry), 'unknown')
        entry.reg_status, entry.status_updated_at = 'weird', datetime.utcnow()
        self.assertEqual(m.display_phone_status(entry), 'unknown')


class MigrationTests(AppTestCase):
    def setUp(self):
        def old_db(path):
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE extension (id INTEGER PRIMARY KEY, extension VARCHAR(20) UNIQUE NOT NULL, title VARCHAR(150) NOT NULL,'
                        ' person_name VARCHAR(150), department_id INTEGER, location VARCHAR(150), email VARCHAR(150), mobile VARCHAR(30),'
                        ' is_present BOOLEAN DEFAULT 0 NOT NULL, notes TEXT, is_active BOOLEAN DEFAULT 1 NOT NULL,'
                        ' is_deleted BOOLEAN DEFAULT 0 NOT NULL, deleted_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)')
            con.execute("INSERT INTO extension (extension, title, created_at, updated_at) VALUES ('410', 'Old Office', '2026-01-01', '2026-01-01')")
            con.commit()
            con.close()

        self.make_app(prepare_db=old_db)

    def test_existing_rows_stay_staff_and_columns_are_added(self):
        row = self.get_ext('410')
        self.assertEqual(row['kind'], 'staff')
        for column in ('mac', 'phone_model', 'reg_status', 'last_ip', 'rtt_ms', 'last_seen_at', 'user_agent', 'status_updated_at'):
            self.assertIn(column, row)
        self.assertIn('Old Office', self.anon.get('/').get_data(as_text=True))


class PublicVisibilityTests(AppTestCase):
    def setUp(self):
        super().setUp()
        now = datetime.utcnow()
        self.add('110', 'Reception Desk', mac='AA:BB:CC:00:11:22', last_ip='10.9.8.7', reg_status='online', status_updated_at=now)
        self.add('201', 'Room Hidden 201', kind='room', mac='AA:BB:CC:00:11:33', last_ip='10.9.8.8', reg_status='online', status_updated_at=now)
        self.add('777', 'Mystery Pending', kind='pending')

    def test_public_page_lists_only_staff(self):
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('Reception Desk', html)
        self.assertNotIn('Room Hidden 201', html)
        self.assertNotIn('Mystery Pending', html)

    def test_rooms_cannot_be_found_by_any_public_filter(self):
        for query in ('?q=201', '?q=Room', '?ext=201', '?title_q=Hidden', '?q=Mystery', '?ext=777'):
            html = self.anon.get('/' + query).get_data(as_text=True)
            self.assertNotIn('Room Hidden 201', html, query)
            self.assertNotIn('Mystery Pending', html, query)

    def test_public_never_shows_ip_or_mac_only_online_state(self):
        html = self.anon.get('/').get_data(as_text=True)
        for secret in ('AA:BB:CC:00:11:22', '10.9.8.7', 'AA:BB:CC:00:11:33', '10.9.8.8'):
            self.assertNotIn(secret, html)
        self.assertIn(m.PHONE_STATUS_LABELS['online'], html)

    def test_no_status_markup_when_there_is_no_pbx_data(self):
        with self.app.app_context():
            for e in m.Extension.query.all():
                e.reg_status = e.status_updated_at = None
            m.db.session.commit()
        html = self.anon.get('/').get_data(as_text=True)
        self.assertNotIn('phone-pill', html)
        self.assertNotIn('وضعیت تلفن', html)

    def test_stale_status_is_shown_as_unknown(self):
        with self.app.app_context():
            e = m.Extension.query.filter_by(extension='110').first()
            e.status_updated_at = datetime.utcnow() - timedelta(minutes=m.PBX_STATUS_STALE_MINUTES + 5)
            m.db.session.commit()
        self.assertIn(m.PHONE_STATUS_LABELS['unknown'], self.anon.get('/').get_data(as_text=True))

    def test_public_exports_exclude_rooms_and_network_columns(self):
        csv_text = self.anon.get('/export/csv').data.decode('utf-8-sig')
        header = next(csv.reader(io.StringIO(csv_text)))
        for column in ('mac', 'kind', 'phone_model', 'email', 'mobile'):
            self.assertNotIn(column, header)
        self.assertIn('Reception Desk', csv_text)
        for secret in ('Room Hidden 201', 'Mystery Pending', 'AA:BB:CC', '10.9.8'):
            self.assertNotIn(secret, csv_text)

    def test_pdfs_contain_staff_only(self):
        captured = []
        fake = lambda output, extensions, font_path: (captured.append([e.extension for e in extensions]), output.write(b'%PDF'))
        with mock.patch.object(m, 'build_extensions_pdf', fake), mock.patch.object(m, 'resolve_pdf_font', lambda configured=None: ('/fake.ttf', None)):
            self.assertEqual(self.anon.get('/export/pdf').status_code, 200)
            self.assertEqual(self.admin.get(self.url('/export/pdf')).status_code, 200)
        self.assertEqual(captured, [['110'], ['110']])

    def test_admin_sees_everything(self):
        html = self.admin.get(self.url('')).get_data(as_text=True)
        for text in ('Reception Desk', 'Room Hidden 201', 'Mystery Pending', 'AA:BB:CC:00:11:22', '10.9.8.8'):
            self.assertIn(text, html)
        only_rooms = self.admin.get(self.url('?kind=room')).get_data(as_text=True)
        self.assertIn('Room Hidden 201', only_rooms)
        self.assertNotIn('Reception Desk', only_rooms)

    def test_admin_can_search_by_mac_and_ip(self):
        for query in ('aa:bb:cc:00:11:33', 'AABBCC001133', '001133', '10.9.8.8'):
            html = self.admin.get(self.url('?q=' + query)).get_data(as_text=True)
            self.assertIn('Room Hidden 201', html, query)
            self.assertNotIn('Reception Desk', html, query)

    def test_admin_export_has_network_columns(self):
        text = self.admin.get(self.url('/export/csv')).data.decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(text)))
        by_ext = {r['extension']: r for r in rows}
        self.assertEqual(by_ext['201']['kind'], 'room')
        self.assertEqual(by_ext['201']['mac'], 'AA:BB:CC:00:11:33')

    def test_admin_pages_require_login(self):
        for path in ('', '/mac-import', '/export/csv'):
            response = self.anon.get(self.url(path))
            self.assertEqual(response.status_code, 302, path)
        self.assertEqual(self.anon.post(self.url('/mac-import/apply')).status_code, 302)


class FormPageTests(AppTestCase):
    def test_new_and_edit_forms_render_with_new_fields(self):
        self.add('120', 'Desk', mac='AA:BB:CC:00:11:22', phone_model='Yealink T46U', last_ip='10.1.2.3', rtt_ms=12,
                 reg_status='online', status_updated_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), user_agent='Yealink SIP-T46U 108.86.0.30')
        entry_id = self.get_ext('120')['id']
        new_html = self.admin.get(self.url('/extensions/new')).get_data(as_text=True)
        for text in ('name="kind"', 'name="mac"', 'name="phone_model"', 'خودکار'):
            self.assertIn(text, new_html)
        edit_html = self.admin.get(self.url(f'/extensions/{entry_id}/edit')).get_data(as_text=True)
        for text in ('AA:BB:CC:00:11:22', 'Yealink T46U', '10.1.2.3', 'Yealink SIP-T46U 108.86.0.30', m.PHONE_STATUS_LABELS['online']):
            self.assertIn(text, edit_html)

    def test_trash_and_mac_import_pages_render(self):
        self.assertEqual(self.admin.get(self.url('/trash')).status_code, 200)
        self.assertEqual(self.admin.get(self.url('/mac-import')).status_code, 200)


class SaveExtensionTests(AppTestCase):
    def post_form(self, **data):
        base = {'extension': '', 'title': 'T', 'kind': 'auto', 'is_active': 'on'}
        base.update(data)
        return self.admin.post(self.url('/extensions/new'), data=base, follow_redirects=True)

    def test_auto_kind_from_number_and_warning(self):
        response = self.post_form(extension='205')
        self.assertEqual(self.get_ext('205')['kind'], 'room')
        self.assertIn('برای پرسنل نمایش داده نمی‌شود', response.get_data(as_text=True))
        self.post_form(extension='120')
        self.assertEqual(self.get_ext('120')['kind'], 'staff')

    def test_explicit_kind_overrides_the_pattern(self):
        self.post_form(extension='410', kind='staff', title='Office F4')
        self.assertEqual(self.get_ext('410')['kind'], 'staff')
        self.assertIn('Office F4', self.anon.get('/').get_data(as_text=True))

    def test_mac_is_normalized_and_validated(self):
        self.post_form(extension='120', mac='aa-bb-cc-dd-ee-ff', phone_model='Yealink T46U')
        row = self.get_ext('120')
        self.assertEqual((row['mac'], row['phone_model']), ('AA:BB:CC:DD:EE:FF', 'Yealink T46U'))
        response = self.post_form(extension='121', mac='not-a-mac')
        self.assertIsNone(self.get_ext('121'))
        self.assertIn('MAC نامعتبر', response.get_data(as_text=True))

    def test_shared_mac_warns_but_saves(self):
        self.post_form(extension='120', mac='AA:BB:CC:DD:EE:FF')
        response = self.post_form(extension='121', mac='aabbccddeeff')
        self.assertIsNotNone(self.get_ext('121'))
        self.assertIn('همین MAC روی داخلی 120', response.get_data(as_text=True))


class DirectoryImportTests(AppTestCase):
    def run_import(self, csv_text):
        data = {'file': (io.BytesIO(csv_text.encode('utf-8-sig')), 'dir.csv')}
        self.admin.post(self.url('/import/preview'), data=data, content_type='multipart/form-data')
        with self.admin.session_transaction() as sess:
            stats = dict(sess['import_stats'])
        self.admin.post(self.url('/import/confirm'))
        return stats

    def test_new_rows_get_kind_from_pattern_and_existing_stay(self):
        self.add('410', 'Old F4 Office')
        stats = self.run_import('extension,title\n410,Renamed F4 Office\n205,Room 205\n130,Back Office\n')
        self.assertEqual(self.get_ext('410')['kind'], 'staff')
        self.assertEqual(self.get_ext('410')['title'], 'Renamed F4 Office')
        self.assertEqual(self.get_ext('205')['kind'], 'room')
        self.assertEqual(self.get_ext('130')['kind'], 'staff')
        self.assertEqual(stats['hidden'], 1)

    def test_optional_columns(self):
        self.add('120', 'Keep Me', mac='11:22:33:44:55:66', phone_model='Old Model')
        stats = self.run_import(
            'extension,title,kind,mac,phone_model\n'
            '120,Keep Me,,,\n'                      # blanks never wipe stored values
            '411,Accounting,پرسنل,aa-bb-cc-dd-ee-ff,Htek UC926\n'
            '130,Bad Values,nonsense,zz:zz,\n')     # invalid values are ignored and counted
        row = self.get_ext('120')
        self.assertEqual((row['mac'], row['phone_model']), ('11:22:33:44:55:66', 'Old Model'))
        row = self.get_ext('411')
        self.assertEqual((row['kind'], row['mac'], row['phone_model']), ('staff', 'AA:BB:CC:DD:EE:FF', 'Htek UC926'))
        row = self.get_ext('130')
        self.assertEqual((row['kind'], row['mac']), ('staff', None))
        self.assertEqual(stats['warnings'], 2)


def make_xlsx(rows, sheet_title='Phones', extra_sheets=()):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    for row in rows:
        sheet.append(row)
    for title, extra_rows in extra_sheets:
        other = workbook.create_sheet(title)
        for row in extra_rows:
            other.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class MacImportTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.add('101', 'Mgmt')
        self.add('102', 'Acc', mac='00:15:65:00:00:02')
        self.add('103', 'HR', mac='00:15:65:00:00:03')
        self.add('104', 'IT')
        self.add('105', 'Trashed', is_deleted=True)

    def upload(self, content, name='macs.xlsx'):
        return self.admin.post(self.url('/mac-import/upload'), data={'file': (io.BytesIO(content), name)}, content_type='multipart/form-data')

    SAMPLE = [
        ['داخلی', 'MAC Address', 'مدل'],
        [101, '00:15:65:AA:00:01', 'Yealink T46U'],   # set
        ['102', '001565000002', ''],                   # same (different format)
        [103, '00:15:65:AA:00:03', ''],                # conflict
        [104, 'bad-mac', ''],                          # invalid
        [999, '00:15:65:AA:00:99', 'Htek UC926'],      # missing, not a room
        [201, '00:15:65:AA:02:01', ''],                # missing, room
        [101, '00:15:65:AA:00:ff', ''],                # duplicate ext in file
        [None, None, None],                            # blank line
        [105, '00:15:65:AA:01:05', ''],                # in trash
        ['', '00:15:65:AA:ff:ff', ''],                 # no extension
        [110, '', ''],                                 # empty MAC
        [111, '00:15:65:AA:00:01', ''],                # same MAC as 101 -> shared warning
    ]

    def test_column_detection_by_header_and_by_content(self):
        for has_header, rows in ((True, self.SAMPLE), (False, [r for r in self.SAMPLE[1:5]])):
            header, body = m.prepare_table(rows, has_header)
            ext_idx, mac_idx, model_idx = m.detect_mac_columns(header, body)
            self.assertEqual((ext_idx, mac_idx), (0, 1), has_header)
        _, body = m.prepare_table([['x', 'y'], ['1', 'Yealink'], ['2', 'Htek']], True)
        self.assertEqual(m.detect_mac_columns(['Room', 'Phone model'], body)[2], 1)

    def test_plan_actions(self):
        header, body = m.prepare_table(self.SAMPLE, True)
        with self.app.app_context():
            plan = m.build_mac_plan(body, 0, 1, 2)
        actions = {(p['row'], p['action']) for p in plan}
        self.assertEqual(actions, {(2, 'set'), (3, 'same'), (4, 'skip_conflict'), (5, 'invalid_mac'), (6, 'skip_missing'), (7, 'skip_missing'),
                                   (8, 'skip_dup_ext'), (10, 'skip_deleted'), (11, 'skip_no_ext'), (12, 'skip_empty_mac'), (13, 'skip_missing')})
        shared = [p['ext'] for p in plan if p.get('shared')]
        self.assertEqual(shared, [])  # 111 is missing here, so nothing is applied for it

    def test_wizard_end_to_end(self):
        self.upload(make_xlsx(self.SAMPLE))
        page = self.admin.get(self.url('/mac-import'))
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('MAC جدید', html)
        self.assertIn('MAC متفاوت', html)
        # apply with the auto-detected columns, no overwrite, no creation
        params = {'configured': '1', 'has_header': '1', 'ext_col': '0', 'mac_col': '1', 'model_col': '2'}
        self.admin.post(self.url('/mac-import/apply'), data=params)
        self.assertEqual(self.get_ext('101')['mac'], '00:15:65:AA:00:01')
        self.assertEqual(self.get_ext('101')['phone_model'], 'Yealink T46U')
        self.assertEqual(self.get_ext('103')['mac'], '00:15:65:00:00:03')      # conflict kept
        self.assertIsNone(self.get_ext('104')['mac'])                           # invalid ignored
        self.assertIsNone(self.get_ext('999'))                                  # not created
        self.assertIsNone(self.get_ext('105')['mac'])                           # trashed untouched
        self.assertEqual(os.listdir(m.IMPORT_TMP_DIR), [])                      # temp file removed
        self.assertEqual(self.admin.get(self.url('/mac-import')).status_code, 200)  # back to the upload stage

    def test_overwrite_and_create_missing(self):
        self.upload(make_xlsx(self.SAMPLE))
        params = {'configured': '1', 'has_header': '1', 'ext_col': '0', 'mac_col': '1', 'model_col': '2', 'overwrite': '1', 'create_missing': '1'}
        self.admin.post(self.url('/mac-import/apply'), data=params)
        self.assertEqual(self.get_ext('103')['mac'], '00:15:65:AA:00:03')
        room = self.get_ext('201')
        self.assertEqual((room['kind'], room['title'], room['mac']), ('room', 'اتاق 201', '00:15:65:AA:02:01'))
        pending = self.get_ext('999')
        self.assertEqual((pending['kind'], pending['phone_model']), ('pending', 'Htek UC926'))
        # created extensions stay hidden from the public
        html = self.anon.get('/').get_data(as_text=True)
        self.assertNotIn('اتاق 201', html)
        self.assertNotIn('داخلی 999', html)

    def test_shared_mac_is_flagged(self):
        rows = [['ext', 'mac'], ['101', '00:15:65:AA:AA:AA'], ['104', '00:15:65:AA:AA:AA']]
        header, body = m.prepare_table(rows, True)
        with self.app.app_context():
            plan = m.build_mac_plan(body, 0, 1, None)
        self.assertEqual([p['ext'] for p in plan if p.get('shared')], ['101', '104'])

    def test_headerless_csv_with_semicolons_and_bom(self):
        csv_bytes = '101;00-15-65-AA-00-01\r\n104;001565aa0004\r\n'.encode('utf-8-sig')
        self.upload(csv_bytes, 'macs.csv')
        html = self.admin.get(self.url('/mac-import')).get_data(as_text=True)
        self.assertIn('MAC جدید', html)
        self.admin.post(self.url('/mac-import/apply'), data={'configured': '1', 'ext_col': '0', 'mac_col': '1'})
        self.assertEqual(self.get_ext('101')['mac'], '00:15:65:AA:00:01')
        self.assertEqual(self.get_ext('104')['mac'], '00:15:65:AA:00:04')

    def test_second_sheet_can_be_selected(self):
        content = make_xlsx([['junk']], extra_sheets=[('Floor 1', [['ext', 'mac'], ['101', '00:15:65:AA:00:01']])])
        self.upload(content)
        self.admin.post(self.url('/mac-import/apply'), data={'configured': '1', 'has_header': '1', 'ext_col': '0', 'mac_col': '1', 'sheet': 'Floor 1'})
        self.assertEqual(self.get_ext('101')['mac'], '00:15:65:AA:00:01')

    def test_bad_uploads_are_rejected(self):
        response = self.upload(b'x', 'notes.txt')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(os.listdir(m.IMPORT_TMP_DIR), [])
        self.assertEqual(self.admin.post(self.url('/mac-import/apply'), data={'configured': '1', 'ext_col': '0', 'mac_col': '1'}).status_code, 302)

    def test_apply_needs_both_columns(self):
        self.upload(make_xlsx(self.SAMPLE))
        self.admin.post(self.url('/mac-import/apply'), data={'configured': '1', 'has_header': '1', 'ext_col': '0'})
        self.assertIsNone(self.get_ext('101')['mac'])


if __name__ == '__main__':
    unittest.main()
