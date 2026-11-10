"""Delivery contract for the Issabel features (stages 1-7).

- every new feature guards at least one route, and every new route is behind a permission;
- dangerous features are never in a non-admin default role;
- every PBX_* setting the code reads is in .env.example and DEPLOYMENT.md;
- the role matrix in DEPLOYMENT.md section 12 matches features.py;
- only one place can write to the PBX (AmiWriter, used by the day/night switch and the safe write test).
"""
import os
import re
import unittest

import features
from tests.test_rbac import AppCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_FEATURES = ('calls.view', 'calls.export', 'dnd.view', 'pbxsync.view', 'pbxhealth.view', 'pbxhealth.alerts',
                'recordings.view', 'recordings.listen', 'recordings.download', 'daynight.view', 'daynight.toggle')
NEW_PATHS = re.compile(r'/(calls|recordings|dnd|pbx-directory|pbx-health|daynight)(/|$)|^/api/pbx/')
CODE = ('app.py', 'pbx_connectors.py', 'pbx_cdr.py', 'pbx_dnd.py', 'pbx_directory_sync.py', 'pbx_health.py', 'pbx_recordings.py',
        'pbx_daynight.py', 'pbx_snapshot.py', 'pbx_sync.py', 'tools/check_pbx.py')
ENV_READ = re.compile(r"""(?:getenv|environ\.get|env\.get|\btext|\bnumber|\bflag|\blisting)\(\s*['"](PBX_[A-Z0-9_]+)['"]""")
LEGACY = {'PBX_SYNC_INTERVAL_SECONDS'}  # old alias, documented next to its new name


def read(path):
    return open(os.path.join(ROOT, path), encoding='utf-8-sig').read()


class RouteContractTests(AppCase):
    def setUp(self):
        super().setUp()
        self.start()

    def test_every_new_feature_guards_a_route_and_every_new_route_has_a_permission(self):
        guarded = {}
        for rule in self.app.url_map.iter_rules():
            view = self.app.view_functions[rule.endpoint]
            path = rule.rule.replace('/' + self.app.config['ADMIN_URL_PREFIX'], '', 1)
            if NEW_PATHS.search(path):
                self.assertEqual(getattr(view, 'access', None), 'permission', rule.rule)
            guarded.setdefault(getattr(view, 'required_permission', None), []).append(rule.endpoint)
        for key in NEW_FEATURES:
            self.assertIn(key, guarded, f'{key} protects no route')


class SettingsContractTests(unittest.TestCase):
    def test_new_features_are_registered_and_dangerous_ones_stay_with_the_admin(self):
        for key in NEW_FEATURES:
            self.assertIn(key, features.FEATURES)
        for _role, _name, _desc, permissions in features.SYSTEM_ROLES:
            for key in permissions or ():
                self.assertFalse(features.FEATURES[key].dangerous and key in NEW_FEATURES, key)
        self.assertTrue({'recordings.listen', 'recordings.download', 'daynight.toggle'} <= {k for k, f in features.FEATURES.items() if f.dangerous})

    def test_every_pbx_setting_is_documented(self):
        used = set()
        for path in CODE:
            used |= set(ENV_READ.findall(read(path)))
        example, deployment = read('.env.example'), read('DEPLOYMENT.md')
        wildcards = re.findall(r'`(PBX_[A-Z0-9_]+)_\*`', deployment)
        self.assertGreater(len(used), 40)
        missing_example = sorted(v for v in used - LEGACY if not re.search(rf'\b{v}\b', example))
        missing_doc = sorted(v for v in used if not re.search(rf'\b{v}\b', deployment) and not any(v.startswith(w + '_') for w in wildcards))
        self.assertEqual(missing_example, [], '.env.example')
        self.assertEqual(missing_doc, [], 'DEPLOYMENT.md')

    def test_example_holds_placeholders_only(self):
        example = read('.env.example')
        for name in ('PBX_AMI_SECRET', 'PBX_AMI_WRITE_SECRET', 'PBX_CDR_PASS', 'PBX_REC_WEB_PASS'):
            value = re.search(rf'^#?\s*{name}=(\S*)', example, re.M).group(1)
            self.assertTrue(value.startswith('<') and value.endswith('>'), name)

    def test_deployment_matrix_matches_features(self):
        section = read('DEPLOYMENT.md').split('### ماتریس نقش × دسترسی (قابلیت‌های تازه)', 1)[1].split('###', 1)[0]
        roles = [perms for _key, _name, _desc, perms in features.SYSTEM_ROLES]
        rows = {}
        for line in section.splitlines():
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            found = re.match(r'`([a-z0-9_.]+)`( ⚠)?$', cells[0]) if cells else None
            if found:
                rows[found.group(1)] = (bool(found.group(2)), [c.startswith('✓') for c in cells[1:]])
        self.assertEqual(sorted(rows), sorted(NEW_FEATURES))
        for key, (dangerous, ticks) in rows.items():
            self.assertEqual(dangerous, features.FEATURES[key].dangerous, key)
            self.assertEqual(ticks, [perms is None or key in perms for perms in roles], key)

    def test_one_place_writes_to_the_pbx(self):
        self.assertEqual(read('app.py').count('open_writer('), 1)              # the day/night switch
        self.assertEqual(read('tools/check_pbx.py').count('open_writer('), 1)  # the HOTELTEST write test
        connectors = read('pbx_connectors.py')
        self.assertEqual(connectors.count('AmiWriter(settings)') + connectors.count('SshWriter(settings)'), 2)  # only in open_writer
        for path in CODE:
            self.assertNotIn('AmiWriter(', read(path)) if path not in ('pbx_connectors.py',) else None
            self.assertNotIn('SshWriter(', read(path)) if path not in ('pbx_connectors.py',) else None
            text = read(path)
            if path not in ('pbx_daynight.py', 'pbx_connectors.py'):
                self.assertNotIn("'DBPut'", text, path)
                self.assertNotIn("'Setvar'", text, path)
            for action in ("'Originate'", "'Command'", "'DBDel", "'Hangup'", "'Redirect'"):
                self.assertNotIn(action, text, f'{action} in {path}')

    def test_delivery_checklist_and_update_steps(self):
        text = read('DEPLOYMENT.md')
        for needle in ('چک‌لیست تحویل روی PBX واقعی', 'nssm stop HotelExtensions', 'pip install -r requirements.txt',
                       'nssm start HotelExtensions', '--test-daynight-write', 'Rollback هر قابلیت'):
            self.assertIn(needle, text)


if __name__ == '__main__':
    unittest.main()
