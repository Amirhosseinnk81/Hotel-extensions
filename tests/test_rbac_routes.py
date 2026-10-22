"""RBAC on the routes: every endpoint has an explicit access decision, and each built-in role gets
exactly what features.SYSTEM_ROLES says (allowed = anything but 403, refused = 403).

Run from the project folder:  python -m unittest discover -s tests -t .
"""
import re
import unittest

import app as m
import features
from tests.test_rbac import LEGACY_PASSWORD, AppCase

PUBLIC = {'set_language', 'robots_txt', 'api_version', 'index', 'login', 'public_export', 'public_export_pdf', 'api_extensions_status'}
LOGIN_ONLY = {'logout', 'change_password'}

# endpoint -> permission. A deliberate second copy of the decisions in app.py: changing who may open a
# route must show up in this file too.
PERMISSIONS = {
    'admin': 'extensions.view',
    'create_extension': 'extensions.create',
    'edit_extension': 'extensions.edit', 'bulk_activate': 'extensions.edit', 'bulk_deactivate': 'extensions.edit',
    'delete_extension': 'extensions.delete', 'bulk_delete': 'extensions.delete',
    'delete_all_extensions': 'extensions.delete_all',
    'import_preview': 'extensions.import', 'import_confirm': 'extensions.import', 'import_cancel': 'extensions.import',
    'mac_import': 'extensions.mac_import', 'mac_import_upload': 'extensions.mac_import',
    'mac_import_apply': 'extensions.mac_import', 'mac_import_cancel': 'extensions.mac_import',
    'export_data': 'extensions.export', 'export_pdf': 'extensions.export',
    'create_department': 'departments.manage', 'edit_department': 'departments.manage', 'delete_department': 'departments.manage',
    'trash': 'trash.view', 'restore_extension': 'trash.restore', 'purge_extension': 'trash.purge',
    'pbx_status': 'pbx.view', 'pbx_sync_now': 'pbx.sync', 'api_pbx_sync_now': 'pbx.sync', 'pbx_set_kind': 'pbx.review',
    'outages_report': 'outages.view', 'outages_export': 'outages.export', 'api_outage_alerts': 'outages.alerts',
    'download_backup': 'backup.download',
    'admin_users': 'users.manage', 'new_user': 'users.manage', 'create_user': 'users.manage', 'edit_user': 'users.manage',
    'reset_user_password': 'users.manage', 'delete_user': 'users.manage', 'unlock_user': 'users.manage',
    'users_access_csv': 'users.manage',
    'admin_roles': 'roles.manage', 'new_role': 'roles.manage', 'edit_role': 'roles.manage', 'delete_role': 'roles.manage',
    'activity_log': 'activity_log.view',
}

MISSING = 999999  # ids that do not exist: an allowed request ends in 404/redirect, a refused one in 403, nothing changes


def requests_to_check(a):
    """(method, path) for every protected endpoint. {e} = a live extension id."""
    return [
        ('GET', f'/{a}'), ('GET', f'/{a}/extensions/new'), ('GET', f'/{a}/extensions/{{e}}/edit'),
        ('POST', f'/{a}/extensions/bulk-activate'), ('POST', f'/{a}/extensions/bulk-deactivate'),
        ('POST', f'/{a}/extensions/{MISSING}/delete'), ('POST', f'/{a}/extensions/bulk-delete'),
        ('POST', f'/{a}/import/preview'), ('POST', f'/{a}/import/confirm'), ('POST', f'/{a}/import/cancel'),
        ('GET', f'/{a}/mac-import'), ('POST', f'/{a}/mac-import/upload'), ('POST', f'/{a}/mac-import/apply'),
        ('POST', f'/{a}/mac-import/cancel'),
        ('GET', f'/{a}/export/csv'), ('GET', f'/{a}/export/pdf'),
        ('POST', f'/{a}/departments'), ('POST', f'/{a}/departments/{MISSING}/edit'), ('POST', f'/{a}/departments/{MISSING}/delete'),
        ('GET', f'/{a}/trash'), ('POST', f'/{a}/extensions/{MISSING}/restore'), ('POST', f'/{a}/extensions/{MISSING}/purge'),
        ('GET', f'/{a}/pbx'), ('POST', f'/{a}/pbx/sync'), ('POST', '/api/pbx/sync-now'), ('POST', f'/{a}/pbx/set-kind'),
        ('GET', f'/{a}/outages'), ('GET', f'/{a}/outages/export.csv'), ('GET', '/api/outages/alerts'),
        ('GET', f'/{a}/backup'),
        ('GET', f'/{a}/users'), ('GET', f'/{a}/users/new'), ('POST', f'/{a}/users'), ('GET', f'/{a}/users/{MISSING}/edit'),
        ('POST', f'/{a}/users/{MISSING}/reset-password'), ('POST', f'/{a}/users/{MISSING}/delete'),
        ('POST', f'/{a}/users/{MISSING}/unlock'), ('GET', f'/{a}/users/access.csv'),
        ('GET', f'/{a}/roles'), ('GET', f'/{a}/roles/new'), ('GET', f'/{a}/roles/{MISSING}/edit'), ('POST', f'/{a}/roles/{MISSING}/delete'),
        ('GET', f'/{a}/activity-log'),
        # destructive last: everything above still sees the extension
        ('POST', f'/{a}/extensions/delete-all'),
    ]


class RoutesCase(AppCase):
    def setUp(self):
        super().setUp()
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            self.role_ids = {r.key: r.id for r in m.Role.query.all()}
            entry = m.Extension(extension='1234', title='Test desk', kind='staff')
            m.db.session.add(entry)
            m.db.session.commit()
            self.entry_id = entry.id

    def endpoint_of(self, method, path):
        return self.app.url_map.bind('localhost').match(path, method=method)[0]

    def add_user(self, name, role, active=True, permissions=None):
        with self.app.app_context():
            role_id = self.role_ids.get(role)
            if permissions is not None:  # a custom role with exactly these keys
                custom = m.Role(key=role, name=role, permissions=[m.RolePermission(permission_key=k) for k in permissions])
                m.db.session.add(custom)
                m.db.session.flush()
                role_id = custom.id
            user = m.User(username=name, display_name=name, role_id=role_id, is_active=active)
            user.set_password(LEGACY_PASSWORD)
            m.db.session.add(user)
            m.db.session.commit()

    def login(self, name):
        client = self.app.test_client()
        response = client.post(f'/{self.prefix}/login', data={'username': name, 'password': LEGACY_PASSWORD})
        return client, response


class ContractTests(RoutesCase):
    def decisions(self):
        result = {}
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint == 'static':
                continue
            view = self.app.view_functions[rule.endpoint]
            result[rule.endpoint] = (getattr(view, 'access', None), getattr(view, 'required_permission', None))
        return result

    def test_every_route_has_an_access_decision(self):
        undecided = sorted(ep for ep, (access, _key) in self.decisions().items() if access not in ('public', 'login', 'permission'))
        self.assertEqual(undecided, [], 'add @require_permission / @login_only / @public_endpoint to these routes')

    def test_public_and_login_only_lists_are_explicit(self):
        decisions = self.decisions()
        self.assertEqual({ep for ep, (a, _k) in decisions.items() if a == 'public'}, PUBLIC)
        self.assertEqual({ep for ep, (a, _k) in decisions.items() if a == 'login'}, LOGIN_ONLY)

    def test_permission_table(self):
        actual = {ep: key for ep, (a, key) in self.decisions().items() if a == 'permission'}
        self.assertEqual(actual, PERMISSIONS)
        self.assertTrue(all(features.is_known(k) for k in actual.values()))

    def test_request_list_covers_every_protected_endpoint(self):
        covered = {self.endpoint_of(method, path.format(e=1)) for method, path in requests_to_check(self.prefix)}
        self.assertEqual(covered, set(PERMISSIONS))

    def test_every_feature_guards_at_least_one_route(self):
        used = {key for access, key in self.decisions().values() if access == 'permission'}
        self.assertEqual(sorted(set(features.FEATURES) - used), [], 'registered features that no route checks')
        self.assertEqual(sorted(used - set(features.FEATURES)), [], 'route keys missing from features.py')

    def test_unknown_key_in_decorator_fails_fast(self):
        with self.assertRaisesRegex(ValueError, 'not registered'):
            m.require_permission('pbx.veiw')


class RoleAccessTests(RoutesCase):
    def check_role(self, role):
        self.add_user('u_' + role, role)
        client, _ = self.login('u_' + role)
        allowed = set(features.FEATURES) if role == features.SUPER_ADMIN else set(
            dict((k, p) for k, _n, _d, p in features.SYSTEM_ROLES)[role])
        for method, path in requests_to_check(self.prefix):
            url = path.format(e=self.entry_id)
            key = PERMISSIONS[self.endpoint_of(method, url)]
            with self.subTest(role=role, method=method, url=url, key=key):
                response = client.open(url, method=method)
                if key in allowed:
                    self.assertNotEqual(response.status_code, 403)
                else:
                    self.assertEqual(response.status_code, 403)
        for url in (f'/{self.prefix}/change-password', '/', '/api/extensions/status'):
            with self.subTest(role=role, url=url):
                self.assertEqual(client.get(url).status_code, 200)
        return client

    def test_super_admin(self):
        self.check_role(features.SUPER_ADMIN)

    def test_operator(self):
        self.check_role('operator')
        with self.app.app_context():
            self.assertFalse(m.db.session.get(m.Extension, self.entry_id).is_deleted, 'refused delete-all changed nothing')

    def test_viewer(self):
        self.check_role('viewer')
        with self.app.app_context():
            self.assertFalse(m.db.session.get(m.Extension, self.entry_id).is_deleted)

    def test_forbidden_page_is_persian_html(self):
        self.add_user('v', 'viewer')
        client, _ = self.login('v')
        response = client.get(f'/{self.prefix}/backup')
        self.assertEqual(response.status_code, 403)
        self.assertIn('text/html', response.content_type)
        self.assertIn('دسترسی ندارید', response.get_data(as_text=True))

    def test_forbidden_api_is_json(self):
        self.add_user('v', 'viewer')
        client, _ = self.login('v')
        response = client.post('/api/pbx/sync-now')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {'ok': False, 'error': 'forbidden'})

    def test_status_api_details_need_pbx_view(self):
        with self.app.app_context():
            entry = m.db.session.get(m.Extension, self.entry_id)
            entry.reg_status, entry.last_ip = 'online', '10.0.0.5'
            entry.status_updated_at = m.datetime.utcnow()
            m.db.session.add(m.Extension(extension='301', title='Room 301', kind='room', reg_status='online',
                                         status_updated_at=m.datetime.utcnow()))
            m.db.session.commit()
        self.add_user('blind', 'nopbx', permissions=['extensions.view'])
        self.add_user('v', 'viewer')
        blind, _ = self.login('blind')
        viewer, _ = self.login('v')
        anon = self.app.test_client()
        for client, detailed in ((anon, False), (blind, False), (viewer, True)):
            statuses = client.get('/api/extensions/status').get_json()['statuses']
            self.assertEqual('301' in statuses, detailed)
            self.assertEqual('ip' in statuses['1234'], detailed)


class AuthFlowTests(RoutesCase):
    def test_anonymous_page_redirects_to_login(self):
        response = self.app.test_client().get(f'/{self.prefix}/pbx')
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/{self.prefix}/login', response.headers['Location'])

    def test_anonymous_api_gets_json_401(self):
        client = self.app.test_client()
        for method, url in (('GET', '/api/outages/alerts'), ('POST', '/api/pbx/sync-now')):
            response = client.open(url, method=method)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json()['error'], 'unauthorized')

    def test_landing_page_follows_permissions(self):
        self.add_user('v', 'viewer')
        self.add_user('watcher', 'outages_only', permissions=['outages.view'])
        self.add_user('nothing', 'empty', permissions=[])
        _, response = self.login('v')
        self.assertTrue(response.headers['Location'].endswith(f'/{self.prefix}'))
        _, response = self.login('watcher')
        self.assertTrue(response.headers['Location'].endswith(f'/{self.prefix}/outages'))
        client, response = self.login('nothing')
        self.assertTrue(response.headers['Location'].endswith('/'))
        self.assertIn('هیچ بخشی', client.get('/').get_data(as_text=True))
        self.assertEqual(client.get(f'/{self.prefix}').status_code, 403)

    def test_deactivated_user_loses_access_on_next_request(self):
        self.add_user('op', 'operator')
        client, _ = self.login('op')
        self.assertEqual(client.get(f'/{self.prefix}/pbx').status_code, 200)
        with self.app.app_context():
            m.User.query.filter_by(username='op').one().is_active = False
            m.db.session.commit()
        response = client.get(f'/{self.prefix}/pbx')
        self.assertEqual(response.status_code, 302)  # Flask-Login treats an inactive user as logged out

    def test_role_change_applies_on_next_request(self):
        self.add_user('op', 'operator')
        client, _ = self.login('op')
        self.assertEqual(client.get(f'/{self.prefix}/backup').status_code, 403)
        with self.app.app_context():
            m.db.session.add(m.UserPermissionOverride(user_id=m.User.query.filter_by(username='op').one().id,
                                                      permission_key='backup.download', effect='grant'))
            m.db.session.commit()
        self.assertEqual(client.get(f'/{self.prefix}/backup').status_code, 200)

    def test_login_and_csrf_still_work(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.add_user('op', 'operator')
        client = self.app.test_client()
        page = client.get(f'/{self.prefix}/login').get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = client.post(f'/{self.prefix}/login', data={'username': 'op', 'password': LEGACY_PASSWORD, 'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get(f'/{self.prefix}').status_code, 200)
        # a POST without the token is still stopped by CSRF before anything else
        response = client.post(f'/{self.prefix}/extensions/delete-all')
        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            self.assertFalse(m.db.session.get(m.Extension, self.entry_id).is_deleted)


if __name__ == '__main__':
    unittest.main()
