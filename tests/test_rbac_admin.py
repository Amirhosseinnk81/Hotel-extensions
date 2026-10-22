"""RBAC in the UI (menu and buttons follow can()) and the user / role management pages with their safeguards.

Run from the project folder:  python -m unittest discover -s tests -t .
"""
import html
import json
import os
import re
import unittest
from unittest import mock

import app as m
import features
from tests.test_rbac import LEGACY_PASSWORD
from tests.test_rbac_routes import RoutesCase

TEMP_PASSWORD = 'temp-pass-42'  # test value only
PBX_WEB_PLACEHOLDER = {'PBX_WEB_HOST': 'https://pbx.test', 'PBX_WEB_USER': 'monitor', 'PBX_WEB_PASS': 'placeholder-pass',
                       'PBX_ALERTS_ENABLED': '1', 'PBX_OUTAGE_MONITOR': '1', 'PBX_SYNC_AUTOSTART': '0'}


class AdminCase(RoutesCase):
    def setUp(self):
        super().setUp()
        with self.app.app_context():  # the seeded admin is the only Super Admin
            root = m.User.query.filter_by(username='admin').one()
            root.set_password(LEGACY_PASSWORD)
            root.must_change_password = False
            m.db.session.commit()
            self.root_id = root.id
        self.root, _ = self.login('admin')

    def url(self, path):
        return f'/{self.prefix}{path}'

    def user(self, name):
        with self.app.app_context():
            u = m.User.query.filter_by(username=name).one()
            return {'id': u.id, 'role': u.role.key if u.role else None, 'active': u.is_active, 'must_change': u.must_change_password,
                    'overrides': {o.permission_key: o.effect for o in u.overrides}}

    def user_id(self, name):
        return self.user(name)['id']

    def logs(self, entity_type):
        with self.app.app_context():
            return [(l.action, l.entity_label, l.details or '') for l in m.ActivityLog.query.filter_by(entity_type=entity_type).order_by(m.ActivityLog.id)]

    def role_keys(self, role):
        return set(dict((k, p) for k, _n, _d, p in features.SYSTEM_ROLES)[role])

    def user_form(self, client, user_id=None, **values):
        data = {'username': values.pop('username', 'newbie'), 'display_name': values.pop('display_name', 'New Person'),
                'role_id': self.role_ids[values.pop('role', 'operator')], 'perm': sorted(values.pop('perm', self.role_keys('operator')))}
        if values.pop('active', True):
            data['is_active'] = '1'
        if user_id is None:
            data['password'] = values.pop('password', TEMP_PASSWORD)
            return client.post(self.url('/users'), data=data)
        return client.post(self.url(f'/users/{user_id}/edit'), data=data)

    def page(self, client, path):
        response = client.get(self.url(path) if path.startswith('/') and not path.startswith(f'/{self.prefix}') else path)
        self.assertEqual(response.status_code, 200, path)
        return response.get_data(as_text=True)


class MenuAndButtonTests(AdminCase):
    SIDEBAR = {'admin': '', 'mac_import': '/mac-import', 'pbx_status': '/pbx', 'outages_report': '/outages', 'trash': '/trash',
               'admin_users': '/users', 'admin_roles': '/roles', 'activity_log': '/activity-log'}

    def sidebar(self, client):
        body = self.page(client, self.url('/outages'))
        nav = re.search(r'<nav class="sb-nav">(.*?)</nav>', body, re.S).group(1)
        return {ep for ep, path in self.SIDEBAR.items() if f'href="/{self.prefix}{path}"' in nav}, nav

    def test_sidebar_per_role(self):
        self.add_user('v', 'viewer')
        self.add_user('op', 'operator')
        expected = {
            'v': {'admin', 'pbx_status', 'outages_report'},
            'op': {'admin', 'pbx_status', 'outages_report', 'trash'},
            'admin': set(self.SIDEBAR),
        }
        for name, endpoints in expected.items():
            client = self.root if name == 'admin' else self.login(name)[0]
            shown, nav = self.sidebar(client)
            with self.subTest(user=name):
                self.assertEqual(shown, endpoints)
                self.assertEqual('سیستم' in nav, name != 'v', 'a group with no visible item is left out')

    def test_new_feature_shows_up_in_the_menu_by_itself(self):
        self.add_user('v', 'viewer')
        viewer, _ = self.login('v')
        with mock.patch.dict(features.FEATURES):
            features.register_feature('voicemail.view', 'صندوق صوتی', features.GROUPS[1],
                                      menu_item=features.MenuItem('index', 'phone', 'voicemail', order=99))
            self.assertIn('صندوق صوتی', self.sidebar(self.root)[1])
            self.assertNotIn('صندوق صوتی', self.sidebar(viewer)[1])

    def test_admin_page_buttons(self):
        self.add_user('v', 'viewer')
        self.add_user('op', 'operator')
        cases = {
            'v': dict(create=False, edit=False, delete=False, bulk=False, export=False, backup=False, import_=False, delete_all=False, depts=False),
            'op': dict(create=False, edit=True, delete=True, bulk=True, export=True, backup=False, import_=False, delete_all=False, depts=True),
            'admin': dict(create=True, edit=True, delete=True, bulk=True, export=True, backup=True, import_=True, delete_all=True, depts=True),
        }
        e = self.entry_id
        with self.app.app_context():
            dept = m.Department.query.first().id
        markers = {
            'create': f'/{self.prefix}/extensions/new"', 'edit': f'/{self.prefix}/extensions/{e}/edit"',
            'delete': f'/{self.prefix}/extensions/{e}/delete"', 'bulk': 'id="bulkForm"', 'export': f'/{self.prefix}/export/xlsx"',
            'backup': f'/{self.prefix}/backup"', 'import_': f'/{self.prefix}/import/preview"', 'delete_all': f'/{self.prefix}/extensions/delete-all"',
            'depts': f'/{self.prefix}/departments/{dept}/delete"',
        }
        for name, expect in cases.items():
            client = self.root if name == 'admin' else self.login(name)[0]
            body = self.page(client, self.url(''))
            for key, visible in expect.items():
                with self.subTest(user=name, button=key):
                    self.assertEqual(markers[key] in body, visible)

    def test_pbx_trash_and_outage_buttons(self):
        with self.app.app_context():
            m.db.session.add(m.Extension(extension='777', title='Pending', kind='pending'))
            m.db.session.add(m.Extension(extension='888', title='Binned', is_deleted=True, deleted_at=m.datetime.utcnow()))
            m.db.session.commit()
            binned = m.Extension.query.filter_by(extension='888').one().id
        self.add_user('v', 'viewer')
        self.add_user('op', 'operator')
        viewer, _ = self.login('v')
        operator, _ = self.login('op')
        with mock.patch.dict(os.environ, PBX_WEB_PLACEHOLDER):  # the sync button only exists once a PBX is configured
            pbx_v, pbx_o = self.page(viewer, self.url('/pbx')), self.page(operator, self.url('/pbx'))
        for marker in (f'/{self.prefix}/pbx/sync"', f'/{self.prefix}/pbx/set-kind"'):
            self.assertNotIn(marker, pbx_v)
            self.assertIn(marker, pbx_o)
        trash = self.page(operator, self.url('/trash'))
        self.assertIn(f'/{self.prefix}/extensions/{binned}/restore"', trash)
        self.assertNotIn(f'/{self.prefix}/extensions/{binned}/purge"', trash)
        self.assertIn(f'/{self.prefix}/extensions/{binned}/purge"', self.page(self.root, self.url('/trash')))
        self.assertNotIn('/outages/export.csv', self.page(viewer, self.url('/outages')))
        self.assertIn('/outages/export.csv', self.page(operator, self.url('/outages')))

    def test_alert_bell_needs_outages_alerts(self):
        self.add_user('v', 'viewer')
        self.add_user('blind', 'no_alerts', permissions=['extensions.view'])
        viewer, _ = self.login('v')
        blind, _ = self.login('blind')
        with mock.patch.dict(os.environ, PBX_WEB_PLACEHOLDER):
            self.assertIn('id="alertToggle"', self.page(viewer, self.url('')))
            body = self.page(blind, self.url(''))
            self.assertNotIn('id="alertToggle"', body)
            self.assertNotIn('data-alerts-url', body)

    def test_role_name_shown_in_sidebar(self):
        self.add_user('op', 'operator')
        self.assertIn('اپراتور', self.page(self.login('op')[0], self.url('')))


class UserAdminTests(AdminCase):
    def test_user_list(self):
        self.add_user('op', 'operator')
        self.add_user('sleepy', 'viewer', active=False)
        self.login('op')
        body = self.page(self.root, self.url('/users'))
        self.assertIn('اپراتور', body)
        self.assertIn('مدیر ارشد', body)
        self.assertIn('غیرفعال', body)
        self.assertNotIn('None', body)

    def test_create_user_with_overrides(self):
        perms = (self.role_keys('operator') - {'pbx.sync'}) | {'activity_log.view'}
        response = self.user_form(self.root, perm=perms)
        self.assertEqual(response.status_code, 302)
        created = self.user('newbie')
        self.assertEqual(created['role'], 'operator')
        self.assertTrue(created['must_change'])
        self.assertEqual(created['overrides'], {'activity_log.view': 'grant', 'pbx.sync': 'deny'})
        action, label, details = self.logs('user')[-1]
        self.assertEqual((action, label), ('created', 'newbie (New Person)'))
        self.assertIn('+activity_log.view', details)
        self.assertIn('+pbx.sync', details)  # in the "denied" part

    def test_password_never_logged(self):
        self.user_form(self.root)
        self.root.post(self.url(f'/users/{self.user_id("newbie")}/reset-password'), data={'password': 'other-temp-99'})
        with self.app.app_context():
            text = ' '.join(f'{l.entity_label} {l.details}' for l in m.ActivityLog.query.all())
        self.assertNotIn(TEMP_PASSWORD, text)
        self.assertNotIn('other-temp-99', text)

    def test_form_page_and_role_json(self):
        self.add_user('op', 'operator')
        body = self.page(self.root, self.url(f'/users/{self.user_id("op")}/edit'))
        roles = json.loads(html.unescape(re.search(r'data-role-perms="([^"]*)"', body).group(1)))
        self.assertIsNone(roles[str(self.role_ids[features.SUPER_ADMIN])])
        self.assertEqual(set(roles[str(self.role_ids['viewer'])]), self.role_keys('viewer'))
        self.assertIn('حساس', body)
        self.assertEqual(body.count('data-perm-group-toggle'), len(features.GROUPS))
        self.assertIn('/reset-password"', body)
        self.assertIn(self.url('/users/new'), self.page(self.root, self.url('/users')))
        self.page(self.root, self.url('/users/new'))

    def test_edit_user_role_change_recomputes_overrides(self):
        self.add_user('op', 'operator')
        uid = self.user_id('op')
        self.user_form(self.root, uid, username='op', perm=self.role_keys('viewer') | {'trash.view'}, role='viewer')
        self.assertEqual(self.user('op'), {**self.user('op'), 'role': 'viewer', 'overrides': {'trash.view': 'grant'}})
        self.assertIn('نقش: اپراتور → مشاهده‌گر', self.logs('user')[-1][2])
        self.user_form(self.root, uid, username='op', perm=[], role=features.SUPER_ADMIN)
        self.assertEqual(self.user('op')['overrides'], {}, 'Super Admin needs no overrides')

    def test_deactivate_user(self):
        self.add_user('op', 'operator')
        self.user_form(self.root, self.user_id('op'), username='op', active=False)
        self.assertFalse(self.user('op')['active'])
        self.assertIn('وضعیت: فعال → غیرفعال', self.logs('user')[-1][2])
        client, _ = self.login('op')
        self.assertEqual(client.get(self.url('')).status_code, 302)

    def test_inactive_login_message(self):
        self.add_user('sleepy', 'viewer', active=False)
        client = self.app.test_client()
        body = client.post(self.url('/login'), data={'username': 'sleepy', 'password': LEGACY_PASSWORD}).get_data(as_text=True)
        self.assertIn('غیرفعال شده است', body)

    def test_validation(self):
        self.add_user('op', 'operator')
        for values, message in ((dict(username='op'), 'قبلاً ثبت شده'), (dict(username='bad name!'), 'نام کاربری باید'),
                                (dict(password='123'), 'حداقل ۶'), (dict(password='admin123'), 'پیش‌فرض')):
            with self.subTest(values=values):
                response = self.user_form(self.root, **values)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.get_data(as_text=True))

    def test_reset_password(self):
        self.add_user('op', 'operator')
        uid = self.user_id('op')
        self.root.post(self.url(f'/users/{uid}/reset-password'), data={'password': 'fresh-temp-77'})
        self.assertTrue(self.user('op')['must_change'])
        client = self.app.test_client()
        response = client.post(self.url('/login'), data={'username': 'op', 'password': 'fresh-temp-77'})
        self.assertTrue(response.headers['Location'].endswith('/change-password'))
        self.assertEqual(self.logs('user')[-1][2], 'ریست رمز عبور توسط مدیر')


class SafeguardTests(AdminCase):
    def manager(self, extra=()):
        """A user manager who is not a Super Admin."""
        self.add_user('mgr', 'manager', permissions=['users.manage', 'extensions.view', 'pbx.view', *extra])
        return self.login('mgr')[0]

    def test_cannot_delete_or_lock_yourself(self):
        self.root.post(self.url(f'/users/{self.root_id}/delete'))
        self.user_form(self.root, self.root_id, username='admin', role='viewer', active=False, perm=[])
        root = self.user('admin')
        self.assertEqual((root['role'], root['active'], root['overrides']), (features.SUPER_ADMIN, True, {}))
        self.assertEqual(self.root.get(self.url('/users')).status_code, 200)
        self.root.post(self.url(f'/users/{self.root_id}/reset-password'), data={'password': 'whatever-123'})
        with self.app.app_context():
            self.assertTrue(m.db.session.get(m.User, self.root_id).check_password(LEGACY_PASSWORD))

    def test_own_form_fields_are_locked(self):
        body = self.page(self.root, self.url(f'/users/{self.root_id}/edit'))
        self.assertRegex(body, r'id="f-role"[^>]*disabled')
        self.assertRegex(body, r'id="f-active"[^>]*disabled')
        self.assertNotIn('/reset-password"', body)

    def test_there_is_always_an_active_super_admin(self):
        self.add_user('root2', features.SUPER_ADMIN)
        root2, _ = self.login('root2')
        self.user_form(self.root, self.user_id('root2'), username='root2', role='viewer')  # allowed: admin stays
        self.assertEqual(self.user('root2')['role'], 'viewer')
        self.user_form(root2, self.root_id, username='admin', role='viewer')  # root2 is no Super Admin any more
        self.assertEqual(self.user('admin')['role'], features.SUPER_ADMIN)
        self.root.post(self.url(f'/users/{self.root_id}/delete'))
        self.assertEqual(self.user('admin')['role'], features.SUPER_ADMIN)

    def test_last_super_admin_check_counts_only_active_ones(self):
        self.add_user('root2', features.SUPER_ADMIN, active=False)
        root2_id = self.user_id('root2')
        self.root.post(self.url(f'/users/{root2_id}/delete'))  # an inactive Super Admin may go
        with self.app.app_context():
            self.assertIsNone(m.db.session.get(m.User, root2_id))

    def test_manager_cannot_touch_super_admins(self):
        mgr = self.manager()
        self.assertEqual(self.user_form(mgr, role=features.SUPER_ADMIN, perm=[]).status_code, 400)
        self.assertEqual(self.user_form(mgr, self.root_id, username='admin', role='viewer', perm=[]).status_code, 400)
        mgr.post(self.url(f'/users/{self.root_id}/delete'))
        mgr.post(self.url(f'/users/{self.root_id}/reset-password'), data={'password': 'hijack-pass-1'})
        root = self.user('admin')
        self.assertEqual((root['role'], root['active']), (features.SUPER_ADMIN, True))
        with self.app.app_context():
            self.assertTrue(m.db.session.get(m.User, self.root_id).check_password(LEGACY_PASSWORD))
        self.assertNotIn(f'<option value="{self.role_ids[features.SUPER_ADMIN]}"', self.page(mgr, self.url('/users/new')))
        self.assertIn(f'<option value="{self.role_ids["viewer"]}"', self.page(mgr, self.url('/users/new')))

    def test_manager_cannot_hand_out_what_they_lack(self):
        mgr = self.manager()
        response = self.user_form(mgr, role='viewer', perm=self.role_keys('viewer') | {'backup.download'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('دانلود بکاپ دیتابیس', response.get_data(as_text=True))
        self.assertEqual(self.user_form(mgr, role='operator').status_code, 400, 'operator has permissions mgr lacks')
        self.assertEqual(self.user_form(mgr, username='ok_user', role='viewer', perm={'extensions.view', 'pbx.view'}).status_code, 302)
        self.assertEqual(self.user('ok_user')['overrides'], {'outages.view': 'deny', 'outages.alerts': 'deny'})

    def test_manager_can_still_edit_a_user_who_has_more(self):
        self.add_user('op', 'operator')
        mgr = self.manager()
        response = self.user_form(mgr, self.user_id('op'), username='op', display_name='Renamed')
        self.assertEqual(response.status_code, 302)  # nothing new handed out


class RoleAdminTests(AdminCase):
    def role_form(self, client, role_id=None, **values):
        data = {'name': values.get('name', 'Night shift'), 'description': values.get('description', ''),
                'perm': sorted(values.get('perm', {'extensions.view', 'pbx.view'}))}
        return client.post(self.url(f'/roles/{role_id}/edit' if role_id else '/roles/new'), data=data)

    def keys_of(self, name):
        with self.app.app_context():
            return m.Role.query.filter_by(name=name).one().permission_keys()

    def test_create_edit_and_delete_custom_role(self):
        self.assertEqual(self.role_form(self.root).status_code, 302)
        self.assertEqual(self.keys_of('Night shift'), {'extensions.view', 'pbx.view'})
        with self.app.app_context():
            role_id = m.Role.query.filter_by(name='Night shift').one().id
        self.role_form(self.root, role_id, perm={'pbx.view', 'outages.view'})  # diff-based update
        self.assertEqual(self.keys_of('Night shift'), {'pbx.view', 'outages.view'})
        self.assertEqual(self.logs('role')[-1][2], 'دسترسی‌ها: +outages.view −extensions.view')
        self.assertIn('Night shift', self.page(self.root, self.url('/roles')))
        self.root.post(self.url(f'/roles/{role_id}/delete'))
        with self.app.app_context():
            self.assertIsNone(m.db.session.get(m.Role, role_id))
        self.assertEqual(self.logs('role')[-1][0], 'deleted')

    def test_copy_role(self):
        body = self.page(self.root, self.url(f'/roles/new?copy={self.role_ids["operator"]}'))
        self.assertIn('کپی اپراتور', body)
        for key in self.role_keys('operator'):
            self.assertRegex(body, rf'value="{re.escape(key)}" data-perm-key checked')
        self.assertNotRegex(body, r'value="backup.download" data-perm-key checked')

    def test_system_roles_are_protected(self):
        self.root.post(self.url(f'/roles/{self.role_ids["viewer"]}/delete'))
        response = self.role_form(self.root, self.role_ids[features.SUPER_ADMIN], name='Hacked', perm=[])
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(m.db.session.get(m.Role, self.role_ids['viewer']))
            self.assertEqual(m.db.session.get(m.Role, self.role_ids[features.SUPER_ADMIN]).name, 'مدیر ارشد')
        self.root.post(self.url(f'/roles/{self.role_ids["viewer"]}/edit'), data={'name': 'Viewer+', 'perm': ['pbx.view']})
        self.assertEqual(self.keys_of('Viewer+'), {'pbx.view'})

    def test_role_with_users_cannot_be_deleted(self):
        self.add_user('n', 'night', permissions=['pbx.view'])
        with self.app.app_context():
            role_id = m.Role.query.filter_by(key='night').one().id
        self.root.post(self.url(f'/roles/{role_id}/delete'))
        with self.app.app_context():
            self.assertIsNotNone(m.db.session.get(m.Role, role_id))

    def test_duplicate_name_and_escalation(self):
        self.assertEqual(self.role_form(self.root, name='اپراتور').status_code, 400)
        self.add_user('rm', 'role_manager', permissions=['roles.manage', 'pbx.view'])
        rm, _ = self.login('rm')
        self.assertEqual(self.role_form(rm, perm={'pbx.view', 'backup.download'}).status_code, 400)
        self.assertEqual(self.role_form(rm, perm={'pbx.view'}).status_code, 302)


if __name__ == '__main__':
    unittest.main()
