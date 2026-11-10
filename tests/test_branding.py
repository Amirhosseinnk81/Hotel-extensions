"""Product name (Araz Desk / AD), the developer credit in the footer of every layout, and the LICENSE file."""
import os
import unittest
from unittest import mock

import app as m
from tests.test_pbx_foundation import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDIT_LINK = '<a href="http://amirhosseinnk.ir/" target="_blank" rel="noopener noreferrer">'


class BrandingTests(AppTestCase):
    def test_public_page(self):
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('| Araz Desk</title>', html)
        self.assertIn('<span class="brand-mark" title="Araz Desk">AD</span>', html)
        self.assertIn('هتل آراز', html)  # the hotel's own name stays
        self.assertIn('طراحی و توسعه:', html)
        self.assertIn(CREDIT_LINK + 'امیرحسین نعیمائی</a>', html)

    def test_admin_shell_and_login_page(self):
        for page in (self.admin.get(self.url('')), self.anon.get(self.url('/login'))):
            html = page.get_data(as_text=True)
            self.assertIn(CREDIT_LINK + 'امیرحسین نعیمائی</a>', html)
            self.assertIn('Araz Desk', html)
        self.assertIn('class="shell-footer', self.admin.get(self.url('')).get_data(as_text=True))

    def test_english_mode(self):
        self.anon.get('/lang/en')
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('Designed &amp; developed by', html)
        self.assertIn(CREDIT_LINK + 'Amirhossein Naeimaei</a>', html)

    def test_defaults_and_overrides(self):
        self.assertEqual((m.APP_NAME, m.APP_MARK), ('Araz Desk', 'AD'))
        with mock.patch.object(m, 'APP_NAME', 'Other Name'), mock.patch.object(m, 'APP_MARK', 'ON'):
            self.make_app()
            html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('| Other Name</title>', html)
        self.assertIn('>ON</span>', html)
        self.assertIn('امیرحسین نعیمائی', html)  # the credit does not depend on the name


class LicenseTests(unittest.TestCase):
    def test_license_file(self):
        text = open(os.path.join(ROOT, 'LICENSE'), encoding='utf-8').read()
        for needle in ('Copyright (c) 2026 Amirhossein Naeimaei', 'All rights reserved', 'Hotel Araz', 'امیرحسین نعیمائی',
                       'without the prior\n   written permission', 'WITHOUT WARRANTY OF ANY KIND'):
            self.assertIn(needle, text)

    def test_readme_and_source_point_at_it(self):
        self.assertIn('[`LICENSE`](LICENSE)', open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read())
        self.assertIn('see the LICENSE file', open(os.path.join(ROOT, 'app.py'), encoding='utf-8').readline())


if __name__ == '__main__':
    unittest.main()
