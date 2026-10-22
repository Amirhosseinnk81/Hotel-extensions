"""Dry-run the roles/permissions (RBAC) migration on a COPY of a database. The file you pass is never written.

    python tools/check_rbac_migration.py C:\\HotelExtensions\\hotel_extensions.db

The database is copied (SQLite backup API) into a temporary folder, the app is started on the copy twice
(the second start must change nothing) and a summary is printed: users with their role, roles with their
number of permissions, rows per table before/after. Exit code 0 = everything as expected.
No passwords or secrets are printed.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def table_counts(path):
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        conn.close()


def main(source):
    if not os.path.isfile(source):
        print(f'File not found: {source}')
        return 2
    saved_env = {k: os.environ.get(k) for k in ('PBX_DOTENV', 'PBX_SYNC_AUTOSTART')}
    os.environ.update({'PBX_DOTENV': '0', 'PBX_SYNC_AUTOSTART': '0'})  # never start the PBX sync thread here
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import app as m
    import features

    saved_paths = (m.BASE_DIR, m.IMPORT_TMP_DIR, m.SECRET_KEY_FILE, m.ADMIN_PREFIX_FILE)
    work = tempfile.mkdtemp(prefix='rbac_check_')
    try:
        copy = os.path.join(work, 'hotel_extensions.db')
        src, dst = sqlite3.connect(f'file:{source}?mode=ro', uri=True), sqlite3.connect(copy)
        src.backup(dst)
        dst.close()
        src.close()
        before = table_counts(copy)
        m.BASE_DIR, m.IMPORT_TMP_DIR = work, os.path.join(work, 'tmp_imports')
        m.SECRET_KEY_FILE, m.ADMIN_PREFIX_FILE = os.path.join(work, '.secret_key'), os.path.join(work, '.admin_prefix')

        def start():
            app = m.create_app()
            for handler in list(app.logger.handlers):  # release logs/app.log in the temp folder
                app.logger.removeHandler(handler)
                handler.close()
            with app.app_context():
                users = [(u.username, u.role.key if u.role else None, u.is_active) for u in m.User.query.order_by(m.User.username)]
                roles = [(r.key, r.name, 'all' if r.is_super_admin else len(r.permission_keys())) for r in m.Role.query.order_by(m.Role.id)]
                m.db.session.remove()
                m.db.engine.dispose()
            return users, roles

        first = start()
        second = start()
        after = table_counts(copy)

        print('Users (username, role, active):')
        for row in first[0]:
            print('  ', row)
        print('Roles (key, name, permissions):')
        for row in first[1]:
            print('  ', row)
        print('Rows per table (before -> after):')
        for name in sorted(set(before) | set(after)):
            print(f'   {name}: {before.get(name, "-")} -> {after.get(name, "-")}')
        backups = os.listdir(os.path.join(work, 'backups')) if os.path.isdir(os.path.join(work, 'backups')) else []
        print('Automatic pre-migration backup:', backups[0] if backups else 'none (already migrated or fresh)')

        problems = []
        if first != second:
            problems.append('the second start changed users or roles (migration is not idempotent)')
        if not any(role == features.SUPER_ADMIN and active for _u, role, active in first[0]):
            problems.append('no active Super Admin')
        if any(role is None for _u, role, _a in first[0]):
            problems.append('some users have no role (they will have no access)')
        lost = [n for n in before if n in after and after[n] < before[n] and n != 'activity_log']
        if lost:
            problems.append('rows disappeared from: ' + ', '.join(lost))
        print('RESULT:', 'OK' if not problems else 'PROBLEMS: ' + '; '.join(problems))
        return 0 if not problems else 1
    finally:
        m.BASE_DIR, m.IMPORT_TMP_DIR, m.SECRET_KEY_FILE, m.ADMIN_PREFIX_FILE = saved_paths
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
