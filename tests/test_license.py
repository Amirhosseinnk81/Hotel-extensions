"""License keys (license_key.py, tools/make_license.py) and the read-only admin panel after a key has ended.

A small key pair is made once for the whole file (1024 bits: fast, test only). tests/__init__.py switches
licensing off for every other test file; here each test points license_key at its own public key file.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

import app as m
import license_key as lk
from tests.test_pbx_connectors import load_tool
from tests.test_pbx_foundation import AppTestCase

PRIVATE, PUBLIC = lk.generate_keypair(1024)
OTHER_PRIVATE, _OTHER_PUBLIC = lk.generate_keypair(1024)
TODAY = date(2026, 11, 10)


def make_key(days=365, grace=14, start=TODAY, private=PRIVATE, customer='Hotel Araz'):
    return lk.sign(private, lk.make_payload(customer, start + timedelta(days=days), start, grace))


class KeyTests(unittest.TestCase):
    def test_round_trip(self):
        payload = lk.verify(PUBLIC, make_key())
        self.assertEqual((payload['customer'], payload['issued'], payload['expires'], payload['grace_days']),
                         ('Hotel Araz', '2026-11-10', '2027-11-10', 14))

    def test_whitespace_from_an_env_file_is_ignored(self):
        key = make_key()
        self.assertEqual(lk.verify(PUBLIC, f'  {key[:40]}\n{key[40:]} ')['customer'], 'Hotel Araz')

    def test_any_change_breaks_the_key(self):
        key = make_key()
        body, signature = key.split('.')
        forged_body = lk._b64(json.dumps({**lk.verify(PUBLIC, key), 'expires': '2099-01-01'}, sort_keys=True,
                                         separators=(',', ':')).encode())
        for bad in (forged_body + '.' + signature, body + '.' + signature[:-4] + 'AAAA', body, body + '.', 'x.y', '', None,
                    key + '.extra', '!' * 50 + '.' + signature, 'A' * 5000 + '.' + signature):
            with self.assertRaises(lk.LicenseError, msg=repr(bad)[:40]):
                lk.verify(PUBLIC, bad)

    def test_key_signed_by_someone_else(self):
        with self.assertRaises(lk.LicenseError):
            lk.verify(PUBLIC, make_key(private=OTHER_PRIVATE))

    def test_signed_but_not_a_license(self):
        body = json.dumps({'product': 'Other', 'customer': 'x', 'issued': '2026-01-01', 'expires': '2027-01-01'}).encode()
        size = (int(PRIVATE['n'], 16).bit_length() + 7) // 8
        signature = pow(int.from_bytes(lk._padded_digest(body, size), 'big'), int(PRIVATE['d'], 16), int(PRIVATE['n'], 16))
        with self.assertRaises(lk.LicenseError):
            lk.verify(PUBLIC, lk._b64(body) + '.' + lk._b64(signature.to_bytes(size, 'big')))

    def test_make_payload_checks(self):
        with self.assertRaises(lk.LicenseError):
            lk.make_payload('', TODAY)
        with self.assertRaises(lk.LicenseError):
            lk.make_payload('x', TODAY - timedelta(days=1), TODAY)

    def test_evaluate_boundaries(self):
        payload = lk.verify(PUBLIC, make_key(days=365, grace=14))
        end = date(2027, 11, 10)
        cases = ((TODAY, 'valid', 365, False), (end - timedelta(days=31), 'valid', 31, False), (end - timedelta(days=30), 'valid', 30, True),
                 (end, 'valid', 0, True), (end + timedelta(days=1), 'grace', -1, False), (end + timedelta(days=14), 'grace', -14, False),
                 (end + timedelta(days=15), 'readonly', -15, False))
        for today, state, days_left, expiring in cases:
            info = lk.evaluate(payload, today)
            self.assertEqual((info['state'], info['days_left'], info['expiring']), (state, days_left, expiring), today)
        self.assertEqual(lk.evaluate(payload, end + timedelta(days=1))['grace_left'], 13)
        self.assertEqual(lk.evaluate(payload, end + timedelta(days=15))['reason'], 'expired')

    def test_no_grace(self):
        payload = lk.verify(PUBLIC, make_key(days=10, grace=0))
        self.assertEqual(lk.evaluate(payload, TODAY + timedelta(days=11))['state'], 'readonly')


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, 'license_public.json')

    def write_public(self, content=None):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(PUBLIC) if content is None else content)

    def test_states(self):
        self.assertEqual(lk.status(make_key(), TODAY, self.path)['state'], 'unmanaged')  # no public key file: nothing is checked
        self.write_public()
        self.assertEqual(lk.status(make_key(), TODAY, self.path)['state'], 'valid')
        self.assertEqual((lk.status('', TODAY, self.path)['state'], lk.status('', TODAY, self.path)['reason']), ('readonly', 'missing'))
        self.assertEqual(lk.status('garbage.key', TODAY, self.path)['reason'], 'invalid')
        self.assertEqual(lk.status(make_key(private=OTHER_PRIVATE), TODAY, self.path)['reason'], 'invalid')
        self.assertEqual(lk.status(make_key(days=1, grace=0), TODAY + timedelta(days=5), self.path)['reason'], 'expired')

    def test_broken_public_key_file_fails_closed(self):
        self.write_public('{not json')
        info = lk.status(make_key(), TODAY, self.path)
        self.assertEqual((info['state'], info['reason']), ('readonly', 'bad_public_key'))


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tool = load_tool('make_license')
        self.private, self.public = os.path.join(self.tmp, 'private.json'), os.path.join(self.tmp, 'public.json')

    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.tool.main(['--private', self.private, '--public', self.public, *args])
        return code, out.getvalue()

    def test_init_issue_show(self):
        code, out = self.run_tool('init', '--bits', '2048')
        self.assertEqual(code, 0)
        self.assertEqual(set(json.load(open(self.public, encoding='utf-8'))), {'n', 'e'})  # no private part in the public file
        self.assertNotIn(json.load(open(self.private, encoding='utf-8'))['d'], out)
        code, out = self.run_tool('issue', '--customer', 'Hotel Araz', '--from', '2026-11-10')
        key = out.split('LICENSE_KEY=')[1].strip()
        self.assertIn('2026-11-10 to 2027-11-10', out)
        self.assertEqual(lk.status(key, TODAY, self.public)['days_left'], 365)
        code, out = self.run_tool('show', key)
        self.assertIn('Valid signature', out)
        with self.assertRaises(SystemExit) as ctx:
            self.run_tool('show', key[:-6] + 'AAAAAA')
        self.assertIn('NOT VALID', str(ctx.exception))

    def test_init_never_overwrites_and_never_writes_into_the_project(self):
        self.run_tool('init', '--bits', '2048')
        with self.assertRaises(SystemExit) as ctx:
            self.run_tool('init', '--bits', '2048')
        self.assertIn('already exists', str(ctx.exception))
        with self.assertRaises(SystemExit) as ctx:
            self.tool.main(['--private', os.path.join(self.tool.ROOT, 'license_private.json'), '--public', self.public + '2', 'init'])
        self.assertIn('inside the project folder', str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tool.ROOT, 'license_private.json')))

    def test_private_key_is_git_ignored(self):
        self.assertIn('license_private*.json', open(os.path.join(self.tool.ROOT, '.gitignore'), encoding='utf-8').read())


# ---------------------------------------------------------------------------- the app
class LicensedAppCase(AppTestCase):
    KEY_DAYS, GRACE, START_OFFSET = 365, 14, 0   # the key starts START_OFFSET days before the real today
    KEY = 'issue'

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = os.path.join(folder, 'license_public.json')
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(PUBLIC, handle)
        patcher = mock.patch.object(lk, 'PUBLIC_KEY_PATH', path)
        patcher.start()
        self.addCleanup(patcher.stop)
        start = m.datetime.now(m.LOCAL_TZ).date() - timedelta(days=self.START_OFFSET)
        key = make_key(self.KEY_DAYS, self.GRACE, start) if self.KEY == 'issue' else self.KEY
        env = mock.patch.dict(os.environ, {'LICENSE_KEY': key})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()

    def new_extension(self, number='4321'):
        return self.admin.post(self.url('/extensions/new'), data={'extension': number, 'title': 'License test', 'kind': 'staff'},
                               headers={'Referer': 'http://localhost' + self.url('/extensions/new')})


class ValidLicenseTests(LicensedAppCase):
    def test_everything_works_and_the_page_shows_the_dates(self):
        self.new_extension()
        self.assertIsNotNone(self.get_ext('4321'))
        page = self.admin.get(self.url('/license')).get_data(as_text=True)
        self.assertIn('معتبر', page)
        self.assertIn('Hotel Araz', page)
        self.assertIn('>365<', page)
        end = m.datetime.now(m.LOCAL_TZ).date() + timedelta(days=365)
        self.assertIn(m.pbx_cdr.jalali_text(end), page)
        self.assertNotIn('license-banner', self.admin.get(self.url('')).get_data(as_text=True))


class ExpiringLicenseTests(LicensedAppCase):
    KEY_DAYS, START_OFFSET = 365, 345  # 20 days left

    def test_warning_banner_but_no_limits(self):
        home = self.admin.get(self.url('')).get_data(as_text=True)
        self.assertIn('license-banner', home)
        self.assertIn('روزهای مانده تا پایان لایسنس نرم‌افزار', home)
        self.new_extension()
        self.assertIsNotNone(self.get_ext('4321'))


class GraceLicenseTests(LicensedAppCase):
    KEY_DAYS, START_OFFSET = 365, 370  # ended 5 days ago, 14 grace days

    def test_grace_keeps_working_with_a_red_banner(self):
        self.assertIn('روزهای مانده تا فقط‌مشاهده شدن پنل', self.admin.get(self.url('')).get_data(as_text=True))
        self.new_extension()
        self.assertIsNotNone(self.get_ext('4321'))
        self.assertIn('پایان‌یافته (مهلت)', self.admin.get(self.url('/license')).get_data(as_text=True))


class ExpiredLicenseTests(LicensedAppCase):
    KEY_DAYS, START_OFFSET = 365, 400  # ended 35 days ago: past the grace days

    def test_admin_is_read_only(self):
        response = self.new_extension()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/extensions/new'))  # back where the user was
        self.assertIsNone(self.get_ext('4321'))
        page = self.admin.get(self.url('')).get_data(as_text=True)
        self.assertIn('فقط قابل مشاهده است', page)
        for path in ('', '/pbx', '/outages', '/activity-log', '/license', '/users', '/extensions/new'):
            self.assertEqual(self.admin.get(self.url(path)).status_code, 200, path)  # looking is allowed
        self.assertEqual(self.admin.get(self.url('/export/csv')).status_code, 200)

    def test_every_protected_change_is_refused(self):
        self.add('100', 'Front desk')
        entry_id = self.get_ext('100')['id']
        for path in (f'/extensions/{entry_id}/delete', '/extensions/delete-all', '/departments', '/users', '/pbx/sync',
                     '/daynight/0/switch', '/dnd/refresh', '/pbx-directory/add'):
            response = self.admin.post(self.url(path), data={'name': 'x'})
            self.assertEqual(response.status_code, 302, path)
        row = self.get_ext('100')
        self.assertFalse(row['is_deleted'])
        with self.app.app_context():
            self.assertEqual(m.Department.query.filter_by(name='x').count(), 0)

    def test_api_gets_a_json_refusal(self):
        response = self.admin.post('/api/pbx/sync-now')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['ok'])

    def test_public_directory_sign_in_and_own_password_still_work(self):
        self.add('100', 'Front desk')
        self.assertIn('Front desk', self.anon.get('/').get_data(as_text=True))
        self.assertEqual(self.anon.get('/api/extensions/status').status_code, 200)
        fresh = self.app.test_client()
        login = fresh.post(self.url('/login'), data={'username': 'admin', 'password': 'test-pass-123'})
        self.assertEqual(login.status_code, 302)
        self.assertEqual(fresh.get(self.url('')).status_code, 200)
        changed = fresh.post(self.url('/change-password'), data={'current_password': 'test-pass-123', 'new_password': 'another-pass-456',
                                                                 'confirm_password': 'another-pass-456'})
        self.assertNotIn('فقط قابل مشاهده', changed.get_data(as_text=True))
        self.assertEqual(fresh.post('/logout').status_code, 302)

    def test_background_sync_is_not_affected(self):
        from tests.test_pbx_sync import FakeClient
        with self.app.app_context():
            summary = m.sync_pbx_once(lambda: FakeClient())
        self.assertGreater(summary['online'], 0)


class MissingKeyTests(LicensedAppCase):
    KEY = ''

    def test_no_key_is_read_only_with_a_clear_reason(self):
        self.new_extension()
        self.assertIsNone(self.get_ext('4321'))
        self.assertIn('LICENSE_KEY', self.admin.get(self.url('/license')).get_data(as_text=True))


class ForeignKeyTests(LicensedAppCase):
    KEY = make_key(private=OTHER_PRIVATE)

    def test_key_from_another_signer_is_refused(self):
        self.new_extension()
        self.assertIsNone(self.get_ext('4321'))
        self.assertIn('پذیرفته نشد', self.admin.get(self.url('/license')).get_data(as_text=True))


class UnmanagedTests(AppTestCase):
    def test_without_a_public_key_nothing_is_checked(self):
        page = self.admin.get(self.url('/license')).get_data(as_text=True)
        self.assertIn('حالت آزمایشی', page)
        self.admin.post(self.url('/extensions/new'), data={'extension': '4321', 'title': 'Free', 'kind': 'staff'})
        self.assertIsNotNone(self.get_ext('4321'))


if __name__ == '__main__':
    unittest.main()
