"""Tests for the .env loader (no Flask needed)."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import envfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def parse(text):
    return dict(envfile.parse_env_text(text)[0])


class ParseTests(unittest.TestCase):
    def test_plain_values_comments_and_blank_lines(self):
        text = '# a comment\n\nPBX_WEB_HOST=https://192.168.1.100\nPBX_SYNC_INTERVAL=30\n   PBX_WEB_USER = monitor  \n'
        self.assertEqual(parse(text), {'PBX_WEB_HOST': 'https://192.168.1.100', 'PBX_SYNC_INTERVAL': '30', 'PBX_WEB_USER': 'monitor'})

    def test_equals_sign_and_hash_inside_a_value_are_kept(self):
        self.assertEqual(parse('PBX_WEB_PASS=ab=cd#ef!\n'), {'PBX_WEB_PASS': 'ab=cd#ef!'})

    def test_inline_comment_needs_a_space_before_the_hash(self):
        self.assertEqual(parse('PBX_SOURCE=web   # use the web panel\n'), {'PBX_SOURCE': 'web'})

    def test_quotes_protect_spaces_and_hashes(self):
        self.assertEqual(parse('A="pass with #hash"\nB=\'single quoted\'  # note\nC="  padded  "\n'),
                         {'A': 'pass with #hash', 'B': 'single quoted', 'C': '  padded  '})

    def test_quote_character_inside_a_quoted_value(self):
        self.assertEqual(parse('A="pa"ss"\n'), {'A': 'pa"ss'})

    def test_empty_value_and_export_prefix(self):
        self.assertEqual(parse('PBX_SOURCE=\nexport PBX_SYNC_INTERVAL=45\n'), {'PBX_SOURCE': '', 'PBX_SYNC_INTERVAL': '45'})

    def test_windows_line_endings_and_a_bom(self):
        self.assertEqual(parse(envfile.decode(b'\xef\xbb\xbfPBX_WEB_USER=IT\r\nPBX_WEB_HOST=https://x\r\n')),
                         {'PBX_WEB_USER': 'IT', 'PBX_WEB_HOST': 'https://x'})

    def test_utf16_file_from_powershell_redirect(self):
        raw = 'PBX_WEB_USER=IT\r\nPBX_WEB_HOST=https://x\r\n'.encode('utf-16')
        self.assertEqual(parse(envfile.decode(raw)), {'PBX_WEB_USER': 'IT', 'PBX_WEB_HOST': 'https://x'})

    def test_persian_text_survives(self):
        self.assertEqual(parse(envfile.decode('ADMIN_URL_PREFIX=پنل-من\n'.encode('utf-8'))), {'ADMIN_URL_PREFIX': 'پنل-من'})

    def test_bad_lines_are_reported_and_skipped(self):
        pairs, problems = envfile.parse_env_text('GOOD=1\nnot a setting\n1BAD=2\nOPEN="never closed\nALSO_GOOD=2\n')
        self.assertEqual(dict(pairs), {'GOOD': '1', 'ALSO_GOOD': '2'})
        self.assertEqual([number for number, _ in problems], [2, 3, 4])


class LoadTests(unittest.TestCase):
    def write(self, text, raw=None):
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: [os.remove(os.path.join(folder, n)) for n in os.listdir(folder)] and os.rmdir(folder))
        path = os.path.join(folder, '.env')
        with open(path, 'wb') as handle:
            handle.write(raw if raw is not None else text.encode('utf-8'))
        return path

    def test_loads_into_the_given_environment(self):
        environ = {}
        loaded = envfile.load_dotenv(self.write('PBX_WEB_HOST=https://x\nPBX_WEB_PASS=s3cret\n'), environ)
        self.assertEqual(environ, {'PBX_WEB_HOST': 'https://x', 'PBX_WEB_PASS': 's3cret'})
        self.assertEqual(loaded, ['PBX_WEB_HOST', 'PBX_WEB_PASS'])

    def test_an_already_set_variable_wins(self):
        environ = {'PBX_WEB_HOST': 'https://from-the-system'}
        loaded = envfile.load_dotenv(self.write('PBX_WEB_HOST=https://from-the-file\nPBX_WEB_USER=u\n'), environ)
        self.assertEqual(environ['PBX_WEB_HOST'], 'https://from-the-system')
        self.assertEqual(environ['PBX_WEB_USER'], 'u')
        self.assertEqual(loaded, ['PBX_WEB_USER'])

    def test_an_empty_system_value_also_wins(self):
        environ = {'PBX_SOURCE': ''}
        envfile.load_dotenv(self.write('PBX_SOURCE=web\n'), environ)
        self.assertEqual(environ['PBX_SOURCE'], '')

    def test_missing_file_is_fine(self):
        environ = {}
        self.assertEqual(envfile.load_dotenv(os.path.join(tempfile.gettempdir(), 'no-such-dir-xyz', '.env'), environ), [])
        self.assertEqual(environ, {})

    def test_directory_instead_of_file_is_fine(self):
        self.assertEqual(envfile.load_dotenv(tempfile.gettempdir(), {}), [])

    def test_switch_off_with_pbx_dotenv(self):
        path = self.write('PBX_WEB_HOST=https://x\n')
        for off in ('0', 'false', 'No', 'OFF'):
            environ = {'PBX_DOTENV': off}
            self.assertEqual(envfile.load_dotenv(path, environ), [])
            self.assertNotIn('PBX_WEB_HOST', environ)

    def test_utf16_and_bom_files_load(self):
        for raw in ('PBX_WEB_USER=IT\r\n'.encode('utf-16'), b'\xef\xbb\xbfPBX_WEB_USER=IT\r\n'):
            environ = {}
            envfile.load_dotenv(self.write('', raw), environ)
            self.assertEqual(environ, {'PBX_WEB_USER': 'IT'})

    def test_loader_never_echoes_values(self):
        loaded = envfile.load_dotenv(self.write('PBX_WEB_PASS=top-secret-value\n'), {})
        self.assertNotIn('top-secret-value', repr(loaded))


class IntegrationTests(unittest.TestCase):
    def test_the_test_package_switches_the_loader_off(self):
        # tests/__init__.py runs first, so a developer's real .env cannot change a test's settings
        self.assertEqual(os.environ.get('PBX_DOTENV'), '0')

    def test_app_loads_the_file_before_it_reads_any_setting(self):
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as handle:
            source = handle.read()
        call = source.index('envfile.load_dotenv(')
        self.assertLess(call, source.index('os.getenv('), 'the .env loader must run before the first os.getenv')
        self.assertLess(source.index('import envfile'), call)

    def test_check_tool_reports_origin_and_hides_secrets(self):
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, '.env'), 'w', encoding='utf-8') as handle:
            handle.write('PBX_WEB_HOST=https://x\nPBX_WEB_PASS=top-secret-value\nPBX_WEB_USR=typo\nbroken line\n')
        # the tool looks next to its own folder, so run a copy laid out like the project
        tool_dir = os.path.join(folder, 'tools')
        os.mkdir(tool_dir)
        for name, source in (('check_env.py', os.path.join(ROOT, 'tools', 'check_env.py')),):
            with open(source, encoding='utf-8') as src, open(os.path.join(tool_dir, name), 'w', encoding='utf-8') as dst:
                dst.write(src.read())
        for name in ('envfile.py', '.env.example'):
            with open(os.path.join(ROOT, name), encoding='utf-8') as src, open(os.path.join(folder, name), 'w', encoding='utf-8') as dst:
                dst.write(src.read())
        env = {k: v for k, v in os.environ.items() if not k.startswith('PBX_')}
        env['PBX_WEB_HOST'] = 'https://already-set'
        result = subprocess.run([sys.executable, os.path.join(tool_dir, 'check_env.py')], env=env, capture_output=True, text=True, timeout=30)
        out = result.stdout
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('top-secret-value', out)
        self.assertIn('<hidden>', out)
        self.assertRegex(out, r'PBX_WEB_HOST\s+https://x\s+already set in the system')
        self.assertRegex(out, r'PBX_WEB_PASS\s+<hidden>\s+from \.env')
        self.assertRegex(out, r'PBX_WEB_USR.*typo')
        self.assertIn('line 4', out)


if __name__ == '__main__':
    unittest.main()
