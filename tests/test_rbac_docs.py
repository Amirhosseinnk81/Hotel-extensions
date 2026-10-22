"""docs/RBAC.md stays true to features.py, and the migration dry-run tool works on a copy only.

Run from the project folder:  python -m unittest discover -s tests -t .
"""
import hashlib
import io
import os
import re
import unittest
from contextlib import redirect_stdout

import features
from tests.test_rbac import AppCase, make_legacy_db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, 'docs', 'RBAC.md')


def doc_matrix():
    text = open(DOC, encoding='utf-8').read()
    block = re.search(r'<!-- role-matrix:start -->(.*?)<!-- role-matrix:end -->', text, re.S).group(1)
    rows = {}
    for line in block.strip().splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        found = re.match(r'`([a-z0-9_.]+)`( ⚠)?$', cells[0])
        if found:
            rows[found.group(1)] = (bool(found.group(2)), [c.startswith('✓') for c in cells[1:]])
    return rows


class DocMatrixTests(unittest.TestCase):
    def test_matrix_lists_every_feature_once(self):
        self.assertEqual(sorted(doc_matrix()), sorted(features.FEATURES))

    def test_matrix_matches_system_roles(self):
        roles = [perms for _key, _name, _desc, perms in features.SYSTEM_ROLES]
        for key, (dangerous, ticks) in doc_matrix().items():
            with self.subTest(key=key):
                self.assertEqual(dangerous, features.FEATURES[key].dangerous, 'the ⚠ mark')
                expected = [perms is None or key in perms for perms in roles]
                self.assertEqual(ticks, expected)

    def test_contract_steps_documented(self):
        text = open(DOC, encoding='utf-8').read()
        for needle in ('register_feature(', '@require_permission(', "can('", 'PERMISSIONS = {', 'requests_to_check',
                       'nssm stop HotelExtensions', 'pip install -r requirements.txt', 'nssm start HotelExtensions',
                       'check_rbac_migration.py', 'Rollback', 'LOGIN_LOCK_ATTEMPTS'):
            self.assertIn(needle, text)


class MigrationToolTests(AppCase):
    def test_dry_run_on_a_copy(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_rbac_migration', os.path.join(ROOT, 'tools', 'check_rbac_migration.py'))
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        make_legacy_db(self.db_path)
        digest = hashlib.sha256(open(self.db_path, 'rb').read()).hexdigest()
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(self.db_path)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn('RESULT: OK', out.getvalue())
        self.assertIn("('alice', 'super_admin', True)", out.getvalue())
        self.assertNotIn('pbkdf2', out.getvalue())  # never prints password hashes
        self.assertEqual(hashlib.sha256(open(self.db_path, 'rb').read()).hexdigest(), digest, 'the source file is never written')
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, 'backups')))


if __name__ == '__main__':
    unittest.main()
