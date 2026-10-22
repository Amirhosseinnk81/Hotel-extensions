"""RBAC foundation: the feature registry (features.py), user_can() and the role migration.

Run from the project folder:  python -m unittest discover -s tests -t .
Every test gets its own temporary database; nothing touches hotel_extensions.db.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from unittest import mock

from werkzeug.security import generate_password_hash

import app as m
import features

LEGACY_PASSWORD = 'legacy-test-pass'  # test value only

# The user table exactly as it was before RBAC (commit 78aa9fc); the other tables are unchanged.
LEGACY_USER_DDL = (
    'CREATE TABLE user (id INTEGER NOT NULL, username VARCHAR(80) NOT NULL, password_hash VARCHAR(255) NOT NULL, '
    'display_name VARCHAR(120) NOT NULL, is_admin BOOLEAN NOT NULL, must_change_password BOOLEAN NOT NULL, PRIMARY KEY (id))',
    'CREATE UNIQUE INDEX ix_user_username ON user (username)',
)


def make_legacy_db(path, usernames=('alice', 'bob')):
    conn = sqlite3.connect(path)
    for stmt in LEGACY_USER_DDL:
        conn.execute(stmt)
    for name in usernames:
        conn.execute('INSERT INTO user (username, password_hash, display_name, is_admin, must_change_password) VALUES (?, ?, ?, 1, 0)',
                     (name, generate_password_hash(LEGACY_PASSWORD), name.title()))
    conn.commit()
    conn.close()


class AppCase(unittest.TestCase):
    """A temp folder that survives several create_app() calls (= service restarts)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        saved = (m.BASE_DIR, m.IMPORT_TMP_DIR, m.SECRET_KEY_FILE, m.ADMIN_PREFIX_FILE)
        m.BASE_DIR = self.tmp
        m.IMPORT_TMP_DIR = os.path.join(self.tmp, 'tmp_imports')
        m.SECRET_KEY_FILE = os.path.join(self.tmp, '.secret_key')
        m.ADMIN_PREFIX_FILE = os.path.join(self.tmp, '.admin_prefix')
        self.db_path = os.path.join(self.tmp, 'hotel_extensions.db')

        def restore():
            m.BASE_DIR, m.IMPORT_TMP_DIR, m.SECRET_KEY_FILE, m.ADMIN_PREFIX_FILE = saved
            shutil.rmtree(self.tmp, ignore_errors=True)

        self.addCleanup(restore)

    def start(self):
        with mock.patch.dict(os.environ, {'PBX_SYNC_AUTOSTART': '0'}):
            app = m.create_app()
        for handler in list(app.logger.handlers):
            if isinstance(handler, RotatingFileHandler):
                app.logger.removeHandler(handler)
                handler.close()
        app.config['WTF_CSRF_ENABLED'] = False
        self.app = app
        return app

    def backups(self):
        folder = os.path.join(self.tmp, 'backups')
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


class RegistryTests(unittest.TestCase):
    EXPECTED = {
        'extensions.view', 'extensions.create', 'extensions.edit', 'extensions.delete', 'extensions.delete_all',
        'extensions.import', 'extensions.mac_import', 'extensions.export', 'departments.manage',
        'pbx.view', 'pbx.sync', 'pbx.review', 'outages.view', 'outages.export', 'outages.alerts',
        'trash.view', 'trash.restore', 'trash.purge', 'backup.download', 'users.manage', 'roles.manage', 'activity_log.view',
    }

    def test_all_current_features_registered(self):
        self.assertEqual(set(features.FEATURES), self.EXPECTED)

    def test_dangerous_flags(self):
        dangerous = {k for k, f in features.FEATURES.items() if f.dangerous}
        self.assertEqual(dangerous, {'extensions.delete_all', 'extensions.import', 'extensions.mac_import', 'trash.purge',
                                     'backup.download', 'users.manage', 'roles.manage'})

    def test_duplicate_key_rejected(self):
        with self.assertRaisesRegex(ValueError, 'registered twice'):
            features.register_feature('outages.view', 'x', features.GROUPS[0])

    def test_bad_key_format_rejected(self):
        for bad in ('outages', 'Outages.view', 'outages.view.all', 'outages-view', '.view', 'outages.', '1x.view', None):
            with self.subTest(key=bad), self.assertRaisesRegex(ValueError, 'Invalid feature key'):
                features.register_feature(bad, 'x', features.GROUPS[0])

    def test_unknown_group_and_missing_label_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown group'):
            features.register_feature('newthing.view', 'x', 'no-such-group')
        with self.assertRaisesRegex(ValueError, 'needs a label'):
            features.register_feature('newthing.view', '', features.GROUPS[0])
        self.assertNotIn('newthing.view', features.FEATURES)

    def test_adding_a_feature_is_one_line(self):
        with mock.patch.dict(features.FEATURES):
            features.register_feature('voicemail.view', 'صندوق صوتی', features.GROUPS[1],
                                      menu_item=features.MenuItem('voicemail', 'phone', 'voicemail'))
            self.assertTrue(features.is_known('voicemail.view'))
            menu_endpoints = [item.endpoint for _g, entries in features.menu() for _f, item in entries]
            self.assertIn('voicemail', menu_endpoints)
            with self.assertRaisesRegex(ValueError, 'voicemail.view -> voicemail'):
                features.check_menu_endpoints({'admin'})
        self.assertFalse(features.is_known('voicemail.view'))

    def test_known_keys_drops_stale_keys(self):
        self.assertEqual(features.known_keys(['pbx.view', 'old.removed', 'garbage']), {'pbx.view'})

    def test_menu_matches_current_sidebar(self):
        menu = [(group, [item.endpoint for _f, item in entries]) for group, entries in features.menu()]
        self.assertEqual(menu, [
            ('دفترچه', ['admin', 'mac_import']),
            ('پایش', ['pbx_status', 'outages_report']),
            ('سیستم', ['trash', 'admin_users', 'admin_roles', 'activity_log']),
        ])

    def test_system_roles(self):
        roles = {key: perms for key, _n, _d, perms in features.SYSTEM_ROLES}
        self.assertIsNone(roles[features.SUPER_ADMIN])
        operator, viewer = set(roles['operator']), set(roles['viewer'])
        self.assertIn('extensions.edit', operator)
        self.assertNotIn('extensions.create', operator)
        self.assertNotIn('activity_log.view', operator)
        self.assertFalse({k for k in operator if features.FEATURES[k].dangerous}, 'operator must not get dangerous permissions')
        self.assertLess(viewer, operator)
        self.assertTrue(all(k.endswith('.view') or k == 'outages.alerts' for k in viewer))


class UserCanTests(AppCase):
    def setUp(self):
        super().setUp()
        self.start()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.roles = {r.key: r for r in m.Role.query.all()}

    def make_user(self, name, role='operator', active=True, overrides=()):
        user = m.User(username=name, display_name=name, role_id=self.roles[role].id if role else None, is_active=active)
        user.set_password(LEGACY_PASSWORD)
        user.overrides = [m.UserPermissionOverride(permission_key=k, effect=e) for k, e in overrides]
        m.db.session.add(user)
        m.db.session.commit()
        m.clear_permission_cache()
        return user

    def test_anonymous_and_none(self):
        from flask_login import AnonymousUserMixin
        self.assertFalse(m.user_can(None, 'pbx.view'))
        self.assertFalse(m.user_can(AnonymousUserMixin(), 'pbx.view'))

    def test_super_admin_has_everything(self):
        admin = m.User.query.filter_by(username='admin').one()
        self.assertTrue(all(m.user_can(admin, k) for k in features.FEATURES))
        self.assertTrue(admin.can('roles.manage'))

    def test_super_admin_gets_future_features(self):
        admin = m.User.query.filter_by(username='admin').one()
        operator = self.make_user('op')
        with mock.patch.dict(features.FEATURES):
            features.register_feature('voicemail.view', 'صندوق صوتی', features.GROUPS[1])
            m.clear_permission_cache()
            self.assertTrue(m.user_can(admin, 'voicemail.view'))
            self.assertFalse(m.user_can(operator, 'voicemail.view'))

    def test_super_admin_ignores_deny(self):
        user = self.make_user('root2', role=features.SUPER_ADMIN, overrides=[('backup.download', 'deny')])
        self.assertTrue(m.user_can(user, 'backup.download'))

    def test_role_permissions(self):
        op = self.make_user('op')
        viewer = self.make_user('viewer1', role='viewer')
        self.assertTrue(m.user_can(op, 'extensions.edit'))
        self.assertFalse(m.user_can(op, 'extensions.create'))
        self.assertFalse(m.user_can(op, 'activity_log.view'))
        self.assertFalse(m.user_can(op, 'backup.download'))
        self.assertTrue(m.user_can(viewer, 'pbx.view'))
        self.assertFalse(m.user_can(viewer, 'pbx.sync'))

    def test_grant_adds_permission(self):
        op = self.make_user('op', overrides=[('activity_log.view', 'grant')])
        self.assertTrue(m.user_can(op, 'activity_log.view'))
        self.assertFalse(m.user_can(op, 'extensions.create'))

    def test_deny_beats_role(self):
        op = self.make_user('op', overrides=[('extensions.delete', 'deny')])
        self.assertFalse(m.user_can(op, 'extensions.delete'))
        self.assertTrue(m.user_can(op, 'extensions.edit'))

    def test_deny_on_permission_role_lacks_is_harmless(self):
        viewer = self.make_user('v', role='viewer', overrides=[('pbx.sync', 'deny')])
        self.assertFalse(m.user_can(viewer, 'pbx.sync'))
        self.assertTrue(m.user_can(viewer, 'pbx.view'))

    def test_inactive_user_gets_nothing(self):
        admin = self.make_user('root2', role=features.SUPER_ADMIN, active=False)
        op = self.make_user('op', active=False, overrides=[('pbx.view', 'grant')])
        self.assertFalse(m.user_can(admin, 'pbx.view'))
        self.assertFalse(m.user_can(op, 'pbx.view'))

    def test_user_without_role_gets_nothing_but_grants_dont_apply_either(self):
        nobody = self.make_user('nobody', role=None, overrides=[('pbx.view', 'grant')])
        self.assertFalse(any(m.user_can(nobody, k) for k in features.FEATURES))

    def test_unknown_key_always_refused(self):
        admin = m.User.query.filter_by(username='admin').one()
        with self.assertLogs('app', level='WARNING'):
            self.assertFalse(m.user_can(admin, 'pbx.veiw'))

    def test_stale_keys_in_database_are_ignored(self):
        operator = self.roles['operator']
        m.db.session.add(m.RolePermission(role_id=operator.id, permission_key='legacy.removed'))
        m.db.session.commit()
        op = self.make_user('op', overrides=[('old.feature', 'grant'), ('older.feature', 'deny'), ('pbx.view', 'bogus')])
        self.assertTrue(m.user_can(op, 'pbx.view'))
        self.assertNotIn('legacy.removed', m.effective_permissions(op)[1])
        self.assertNotIn('old.feature', m.effective_permissions(op)[1])

    def test_result_cached_per_request(self):
        op_id = self.make_user('op').id
        with self.app.app_context(), self.app.test_request_context():  # a request = a fresh app context (own g)
            op = m.db.session.get(m.User, op_id)
            self.assertTrue(m.user_can(op, 'pbx.sync'))
            m.db.session.add(m.UserPermissionOverride(user_id=op_id, permission_key='pbx.sync', effect='deny'))
            m.db.session.commit()
            self.assertTrue(m.user_can(op, 'pbx.sync'), 'same request: cached value')
        with self.app.app_context(), self.app.test_request_context():
            self.assertFalse(m.user_can(m.db.session.get(m.User, op_id), 'pbx.sync'), 'next request sees the change')

    def test_one_query_set_per_request(self):
        op = self.make_user('op')
        with self.app.test_request_context():
            with mock.patch.object(m, 'UserPermissionOverride', wraps=m.UserPermissionOverride) as wrapped:
                for key in features.FEATURES:
                    m.user_can(op, key)
                self.assertLessEqual(len([c for c in wrapped.mock_calls if 'query' in str(c)]), 1)

    def test_deleting_user_removes_overrides(self):
        op = self.make_user('op', overrides=[('pbx.view', 'deny')])
        m.db.session.delete(op)
        m.db.session.commit()
        self.assertEqual(m.UserPermissionOverride.query.count(), 0)

    def test_inactive_user_cannot_log_in(self):
        self.make_user('sleepy', active=False)
        client = self.app.test_client()
        prefix = self.app.config['ADMIN_URL_PREFIX']
        client.post(f'/{prefix}/login', data={'username': 'sleepy', 'password': LEGACY_PASSWORD})
        self.assertEqual(client.get(f'/{prefix}').status_code, 302)


class MigrationTests(AppCase):
    def users(self):
        with self.app.app_context():
            return {u.username: (u.role.key if u.role else None, u.is_active) for u in m.User.query.all()}

    def test_fresh_install(self):
        self.start()
        self.assertEqual(self.users(), {'admin': (features.SUPER_ADMIN, True)})
        with self.app.app_context():
            self.assertEqual({r.key for r in m.Role.query.all()}, {features.SUPER_ADMIN, 'operator', 'viewer'})
            self.assertTrue(all(r.is_system for r in m.Role.query.all()))
            self.assertEqual(m.Role.query.filter_by(key=features.SUPER_ADMIN).one().permissions, [])
        self.assertEqual(self.backups(), [], 'nothing to back up on a fresh install')

    def test_legacy_users_become_super_admin(self):
        make_legacy_db(self.db_path)
        self.start()
        self.assertEqual(self.users(), {'alice': (features.SUPER_ADMIN, True), 'bob': (features.SUPER_ADMIN, True)})
        with self.app.app_context():
            self.assertEqual(m.ActivityLog.query.filter_by(actor='system', entity_type='user').count(), 1)

    def test_backup_taken_before_migration(self):
        make_legacy_db(self.db_path)
        self.start()
        names = self.backups()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith('hotel_extensions_pre_rbac_'))
        conn = sqlite3.connect(os.path.join(self.tmp, 'backups', names[0]))
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            cols = {r[1] for r in conn.execute('PRAGMA table_info(user)')}
            count = conn.execute('SELECT COUNT(*) FROM user').fetchone()[0]
        finally:
            conn.close()
        self.assertNotIn('role', tables)
        self.assertNotIn('role_id', cols)
        self.assertEqual(count, 2)

    def test_failed_backup_stops_the_migration(self):
        make_legacy_db(self.db_path)
        with mock.patch.object(m, 'make_db_backup', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.start()
        conn = sqlite3.connect(self.db_path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertNotIn('role', tables)

    def test_restart_is_idempotent_and_keeps_admin_edits(self):
        make_legacy_db(self.db_path)
        self.start()
        with self.app.app_context():
            operator = m.Role.query.filter_by(key='operator').one()
            operator.permissions = []  # edited by an admin: only pbx.view left
            m.db.session.flush()
            operator.permissions = [m.RolePermission(permission_key='pbx.view')]
            bob = m.User.query.filter_by(username='bob').one()
            bob.role_id = operator.id
            stray = m.User(username='stray', display_name='Stray', role_id=None)  # e.g. inserted by hand
            stray.set_password(LEGACY_PASSWORD)
            m.db.session.add(stray)
            m.db.session.commit()
        self.start()
        self.start()
        self.assertEqual(self.users(), {'alice': (features.SUPER_ADMIN, True), 'bob': ('operator', True), 'stray': (None, True)})
        with self.app.app_context():
            self.assertEqual(m.Role.query.count(), 3)
            self.assertEqual(m.Role.query.filter_by(key='operator').one().permission_keys(), {'pbx.view'})
            self.assertEqual(m.ActivityLog.query.filter_by(actor='system', entity_type='user').count(), 1)
        self.assertEqual(len(self.backups()), 1, 'only the first start makes a backup')

    def test_deleted_system_role_is_recreated(self):
        self.start()
        with self.app.app_context():
            m.db.session.delete(m.Role.query.filter_by(key='viewer').one())
            m.db.session.commit()
        self.start()
        with self.app.app_context():
            viewer = m.Role.query.filter_by(key='viewer').one()
            self.assertEqual(viewer.permission_keys(), set(dict((k, p) for k, _n, _d, p in features.SYSTEM_ROLES)['viewer']))

    def test_existing_login_still_works_after_migration(self):
        make_legacy_db(self.db_path)
        app = self.start()
        client = app.test_client()
        prefix = app.config['ADMIN_URL_PREFIX']
        response = client.post(f'/{prefix}/login', data={'username': 'alice', 'password': LEGACY_PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get(f'/{prefix}').status_code, 200)
        with app.app_context():
            self.assertIsNotNone(m.User.query.filter_by(username='alice').one().last_login_at)

    def test_old_code_can_still_read_new_database(self):
        """Rollback by code only: the pre-RBAC queries keep working on the migrated file."""
        make_legacy_db(self.db_path)
        self.start()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('INSERT INTO user (username, password_hash, display_name, is_admin, must_change_password) VALUES (?, ?, ?, 1, 0)',
                         ('carol', 'x', 'Carol'))
            rows = conn.execute('SELECT id, username, password_hash, display_name, is_admin, must_change_password FROM user').fetchall()
            active = conn.execute("SELECT is_active FROM user WHERE username='carol'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(len(rows), 3)
        self.assertEqual(active, 1)


if __name__ == '__main__':
    unittest.main()
