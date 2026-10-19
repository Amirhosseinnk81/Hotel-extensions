"""Public page structure: tile grid, native detail dialog, plain advanced-search toggle, refresh button only for
signed-in staff, and the CDN fallback for Bootstrap files that are missing from static/vendor/."""
import os
import tempfile
import unittest
from unittest import mock

import app as m
import tests.test_pbx_web_sync as web


class PublicUiTests(web.WebCase):
    def page(self, client=None):
        return (client or self.anon).get('/').get_data(as_text=True)

    def test_tiles_dialog_and_search_markup(self):
        html = self.page()
        for needle in ('class="ext-grid" id="cardsView"', 'data-card-wrapper', 'class="extension-card"', '<dialog class="detail-dialog',
                       'id="extensionDetailModal"', 'data-detail-close', 'class="search-grid"', 'id="advancedToggle"', 'id="advancedSearch" hidden'):
            self.assertIn(needle, html)
        for old in ('data-bs-toggle', 'data-bs-target', 'class="modal fade'):
            self.assertNotIn(old, html)   # nothing on this page needs Bootstrap's JavaScript any more

    def test_tile_keeps_the_hooks_the_script_uses(self):
        html = self.page()
        for hook in ('data-ext="100"', 'fav-btn', 'copy-btn', 'card-status-col', 'role="button"', 'tabindex="0"'):
            self.assertIn(hook, html)

    def test_advanced_panel_starts_open_when_a_field_is_filled(self):
        html = self.anon.get('/?person=Ali').get_data(as_text=True)
        self.assertNotIn('id="advancedSearch" hidden', html)
        self.assertIn('aria-expanded="true"', html)

    def test_refresh_button_is_for_signed_in_staff_only(self):
        self.use_env()
        public = self.page()
        self.assertIn('id="liveBar"', public)
        self.assertNotIn('id="liveRefresh"', public)
        self.assertIn('id="liveRefresh"', self.page(self.admin))


class VendorFallbackTests(web.WebCase):
    def with_base_dir(self, folder):
        patcher = mock.patch.object(m, 'BASE_DIR', folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_folder(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(self.remove_tree, folder)
        return folder

    @staticmethod
    def remove_tree(folder):
        for root, _, names in os.walk(folder, topdown=False):
            for name in names:
                os.remove(os.path.join(root, name))
            os.rmdir(root)

    def test_missing_vendor_files_fall_back_to_the_cdn(self):
        self.with_base_dir(self.make_folder())
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css', html)
        self.assertIn('https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js', html)

    def test_local_files_are_used_when_they_exist(self):
        folder = self.make_folder()
        os.makedirs(os.path.join(folder, 'static', 'vendor'))
        for name in ('bootstrap.rtl.min.css', 'bootstrap.bundle.min.js'):
            with open(os.path.join(folder, 'static', 'vendor', name), 'wb') as handle:
                handle.write(b'/*!' + b'x' * 2000)
        self.with_base_dir(folder)
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('/static/vendor/bootstrap.rtl.min.css', html)
        self.assertIn('/static/vendor/bootstrap.bundle.min.js', html)
        self.assertNotIn('cdn.jsdelivr.net/npm/bootstrap', html)

    def test_an_empty_or_tiny_local_file_does_not_count(self):
        folder = self.make_folder()
        os.makedirs(os.path.join(folder, 'static', 'vendor'))
        with open(os.path.join(folder, 'static', 'vendor', 'bootstrap.min.css'), 'wb') as handle:
            handle.write(b'')
        self.with_base_dir(folder)
        with self.app.test_request_context():
            self.assertTrue(m.vendor_asset_url('bootstrap.min.css').startswith('https://cdn.jsdelivr.net/'))


if __name__ == '__main__':
    unittest.main()
