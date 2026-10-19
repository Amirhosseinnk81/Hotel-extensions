"""Tests for tools/fetch_vendor.py (no network, no Flask)."""
import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_spec = importlib.util.spec_from_file_location('fetch_vendor', os.path.join(ROOT, 'tools', 'fetch_vendor.py'))
fetch_vendor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_vendor)

FILES = [
    ('bootstrap.rtl.min.css', 'https://cdn.example/b.css', b'/*!', 100),
    ('fonts/vazirmatn-arabic-wght-normal.woff2', 'https://cdn.example/f.woff2', b'wOF2', 50),
]
GOOD = {'https://cdn.example/b.css': b'/*! Bootstrap */' + b'x' * 200, 'https://cdn.example/f.woff2': b'wOF2' + b'\0' * 100}


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)
        self.calls, self.messages = [], []

    def cleanup(self):
        for folder, _, names in os.walk(self.dir, topdown=False):
            for name in names:
                os.remove(os.path.join(folder, name))
            os.rmdir(folder)

    def run_fetch(self, table):
        def download(url):
            self.calls.append(url)
            value = table[url]
            if isinstance(value, Exception):
                raise value
            return value
        return fetch_vendor.fetch_all(self.dir, FILES, download, self.messages.append)

    def test_saves_good_files_including_the_fonts_subfolder(self):
        self.assertEqual(self.run_fetch(GOOD), [])
        self.assertTrue(os.path.isfile(os.path.join(self.dir, 'bootstrap.rtl.min.css')))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, 'fonts', 'vazirmatn-arabic-wght-normal.woff2')))

    def test_an_error_page_is_not_saved_as_a_stylesheet(self):
        table = dict(GOOD, **{'https://cdn.example/b.css': b'<html>404 Not Found</html>' + b' ' * 300})
        self.assertEqual(self.run_fetch(table), ['bootstrap.rtl.min.css'])
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'bootstrap.rtl.min.css')))

    def test_a_truncated_download_is_rejected(self):
        table = dict(GOOD, **{'https://cdn.example/f.woff2': b'wOF2'})
        self.assertEqual(self.run_fetch(table), ['fonts/vazirmatn-arabic-wght-normal.woff2'])

    def test_network_errors_are_reported_and_the_rest_still_downloads(self):
        table = dict(GOOD, **{'https://cdn.example/b.css': OSError('network is unreachable')})
        self.assertEqual(self.run_fetch(table), ['bootstrap.rtl.min.css'])
        self.assertTrue(os.path.isfile(os.path.join(self.dir, 'fonts', 'vazirmatn-arabic-wght-normal.woff2')))
        self.assertTrue(any('OSError' in line for line in self.messages))

    def test_good_files_that_are_already_there_are_not_downloaded_again(self):
        self.run_fetch(GOOD)
        self.calls.clear()
        self.assertEqual(self.run_fetch(GOOD), [])
        self.assertEqual(self.calls, [])

    def test_a_damaged_existing_file_is_replaced(self):
        with open(os.path.join(self.dir, 'bootstrap.rtl.min.css'), 'wb') as handle:
            handle.write(b'broken')
        self.assertEqual(self.run_fetch(GOOD), [])
        with open(os.path.join(self.dir, 'bootstrap.rtl.min.css'), 'rb') as handle:
            self.assertTrue(handle.read().startswith(b'/*!'))

    def test_the_real_file_list_matches_what_the_site_asks_for(self):
        names = {name for name, *_ in fetch_vendor.FILES}
        self.assertTrue({'bootstrap.min.css', 'bootstrap.rtl.min.css', 'bootstrap.bundle.min.js'} <= names)
        with open(os.path.join(ROOT, 'static', 'style.css'), encoding='utf-8') as handle:
            css = handle.read()
        for name in names:
            if name.startswith('fonts/'):
                self.assertIn('vendor/' + name, css)   # the @font-face in style.css points at the same path


if __name__ == '__main__':
    unittest.main()
