"""Hardening: temporary account lockout, sign-out of deactivated users, and the "who has access to what" CSV.

Run from the project folder:  python -m unittest discover -s tests -t .
"""
import csv
import io
import unittest
from unittest import mock

import app as m
import features
from tests.test_rbac import LEGACY_PASSWORD
from tests.test_rbac_admin import AdminCase


class LockoutCase(AdminCase):
    def setUp(self):
        for store in (m._login_failures, m._account_failures, m._account_locks):
            store.clear()
        self.addCleanup(lambda: [s.clear() for s in (m._login_failures, m._account_failures, m._account_locks)])
        super().setUp()
        self.attempt = 0

    def try_login(self, username, password):
        """Every attempt from a different IP, so only the per-account lock can stop it."""
        self.attempt += 1
        client = self.app.test_client()
        response = client.post(self.url('/login'), data={'username': username, 'password': password},
                               environ_base={'REMOTE_ADDR': f'10.0.0.{self.attempt}'})
        return client, response


class LockoutTests(LockoutCase):
    def fail(self, username, times=m.LOGIN_LOCK_ATTEMPTS):
        for _ in range(times):
            self.try_login(username, 'wrong-password')

    def test_account_locks_after_repeated_failures_from_any_ip(self):
        self.add_user('op', 'operator')
        self.fail('op', m.LOGIN_LOCK_ATTEMPTS - 1)
        _, response = self.try_login('op', LEGACY_PASSWORD)
        self.assertEqual(response.status_code, 302, 'still below the limit')
        self.fail('op')
        _, response = self.try_login('op', LEGACY_PASSWORD)
        self.assertEqual(response.status_code, 200)
        self.assertIn('موقتاً قفل شده', response.get_data(as_text=True))

    def test_success_resets_the_counter(self):
        self.add_user('op', 'operator')
        self.fail('op', m.LOGIN_LOCK_ATTEMPTS - 1)
        self.try_login('op', LEGACY_PASSWORD)
        self.fail('op', m.LOGIN_LOCK_ATTEMPTS - 1)
        _, response = self.try_login('op', LEGACY_PASSWORD)
        self.assertEqual(response.status_code, 302)

    def test_unknown_username_behaves_the_same(self):
        self.fail('ghost')
        _, response = self.try_login('ghost', 'anything')
        self.assertIn('موقتاً قفل شده', response.get_data(as_text=True))
        self.assertEqual([l for l in self.logs('user') if 'قفل' in l[2]], [], 'nothing to log for a missing account')

    def test_lock_is_case_insensitive_and_logged_once(self):
        self.add_user('op', 'operator')
        self.fail('OP')
        _, response = self.try_login('op', LEGACY_PASSWORD)
        self.assertIn('موقتاً قفل شده', response.get_data(as_text=True))
        locks = [l for l in self.logs('user') if 'قفل موقت ورود' in l[2]]
        self.assertEqual(len(locks), 1)
        self.assertNotIn('wrong-password', locks[0][2])

    def test_lock_expires(self):
        self.add_user('op', 'operator')
        self.fail('op')
        later = m.time.time() + m.LOGIN_LOCK_MINUTES * 60 + 1
        with mock.patch.object(m.time, 'time', return_value=later):
            _, response = self.try_login('op', LEGACY_PASSWORD)
        self.assertEqual(response.status_code, 302)

    def test_admin_can_unlock_and_reset_unlocks(self):
        self.add_user('op', 'operator')
        self.fail('op')
        listing = self.page(self.root, self.url('/users'))
        self.assertIn('قفل موقت', listing)
        self.root.post(self.url(f'/users/{self.user_id("op")}/unlock'))
        self.assertEqual(self.try_login('op', LEGACY_PASSWORD)[1].status_code, 302)
        self.assertEqual(self.logs('user')[-1][2], 'باز کردن قفل موقت ورود')
        self.fail('op')
        self.root.post(self.url(f'/users/{self.user_id("op")}/reset-password'), data={'password': 'fresh-temp-77'})
        self.assertEqual(self.try_login('op', 'fresh-temp-77')[1].status_code, 302)

    def test_can_be_switched_off(self):
        self.add_user('op', 'operator')
        with mock.patch.object(m, 'LOGIN_LOCK_ATTEMPTS', 0):
            self.fail('op', 10)
            self.assertEqual(self.try_login('op', LEGACY_PASSWORD)[1].status_code, 302)


class DeactivationTests(LockoutCase):
    def test_deactivated_user_is_signed_out(self):
        self.add_user('op', 'operator')
        client, _ = self.try_login('op', LEGACY_PASSWORD)
        self.assertEqual(client.get(self.url('/pbx')).status_code, 200)
        self.user_form(self.root, self.user_id('op'), username='op', active=False)
        response = client.get('/')
        self.assertIn('از سامانه خارج شدید', response.get_data(as_text=True))
        with client.session_transaction() as sess:
            self.assertNotIn('_user_id', sess)
        # re-activating does not bring the old session back
        self.user_form(self.root, self.user_id('op'), username='op', active=True)
        self.assertEqual(client.get(self.url('/pbx')).status_code, 302)

    def test_api_call_signs_out_without_flash(self):
        self.add_user('op', 'operator')
        client, _ = self.try_login('op', LEGACY_PASSWORD)
        self.user_form(self.root, self.user_id('op'), username='op', active=False)
        self.assertEqual(client.get('/api/outages/alerts').status_code, 401)
        with client.session_transaction() as sess:
            self.assertNotIn('_user_id', sess)
            self.assertFalse(sess.get('_flashes'))


class AccessReportTests(AdminCase):
    def rows(self, client):
        response = client.get(self.url('/users/access.csv'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'), 'UTF-8 BOM for Excel')
        return list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))

    def test_report_contents(self):
        self.add_user('op', 'operator')
        self.add_user('v', 'viewer', active=False)
        self.user_form(self.root, self.user_id('op'), username='op', display_name='=HYPERLINK("x")',
                       perm=(self.role_keys('operator') - {'pbx.sync'}) | {'activity_log.view'})
        rows = {r['username']: r for r in self.rows(self.root)}
        self.assertEqual(list(rows['op'])[:5], ['username', 'display_name', 'role', 'active', 'last_login'])
        self.assertEqual(list(rows['op'])[5:], list(features.FEATURES))
        op = rows['op']
        self.assertEqual(op['display_name'], '\'=HYPERLINK("x")', 'csv_safe defuses formulas')
        self.assertEqual((op['extensions.edit'], op['activity_log.view'], op['pbx.sync'], op['backup.download']), ('role', 'grant', 'deny', ''))
        self.assertEqual(rows['v']['active'], 'no')
        self.assertEqual(rows['v']['pbx.view'], 'role')
        self.assertEqual({rows['admin'][k] for k in features.FEATURES}, {'super'})
        self.assertEqual(self.logs('user')[-1][:2], ('exported', 'گزارش دسترسی کاربران (CSV)'))

    def test_report_needs_users_manage(self):
        self.add_user('op', 'operator')
        client, _ = self.login('op')
        self.assertEqual(client.get(self.url('/users/access.csv')).status_code, 403)


if __name__ == '__main__':
    unittest.main()
