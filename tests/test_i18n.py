"""Persian/English UI: dictionary coverage, language switching and safety of stored values."""
import glob
import os
import re
import unittest

import app as m
import i18n
from tests.test_pbx_foundation import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FA = re.compile('[\u0600-\u06FF]')


def template_keys():
    keys = set()
    for path in glob.glob(os.path.join(ROOT, 'templates', '*.html')):
        text = open(path, encoding='utf-8').read()
        for q, body in re.findall(r"""\bt\((['"])((?:(?!\1)[^\\]|\\.)*)\1\)""", text):
            keys.add(body.replace("\\'", "'").replace('\\\\', '\\'))
    return keys


class DictionaryTests(unittest.TestCase):
    def test_every_template_string_has_an_english_translation(self):
        missing = sorted(k for k in template_keys() if FA.search(k) and i18n.norm(k).strip() not in i18n.EN)
        self.assertEqual(missing, [])

    def test_every_javascript_string_has_an_english_translation(self):
        text = open(os.path.join(ROOT, 'static', 'app.js'), encoding='utf-8').read()
        keys = {i18n.norm(m_.group(2).strip()) for m_ in re.finditer(r"""(['"])((?:(?!\1).)*[\u0600-\u06FF](?:(?!\1).)*)\1""", text)}
        self.assertEqual(sorted(k for k in keys if k not in i18n.EN), [])

    def test_page_titles_are_english(self):
        for path in glob.glob(os.path.join(ROOT, 'templates', '*.html')):
            text = open(path, encoding='utf-8').read()
            for title in re.findall(r'<title>(.*?)</title>|\{% block title %\}(.*?)\{% endblock %\}', text, re.S):
                self.assertFalse(FA.search(''.join(title)), path)

    def test_translate_is_a_noop_in_persian_and_ignores_zwnj(self):
        self.assertEqual(i18n.translate('داخلی‌های هتل', 'fa'), 'داخلی‌های هتل')
        self.assertEqual(i18n.translate('داخلی‌های هتل', 'en'), i18n.translate('داخلیهای هتل', 'en'))
        self.assertEqual(i18n.translate('متن ناشناخته', 'en'), 'متن ناشناخته')

    def test_dynamic_messages(self):
        self.assertEqual(i18n.translate('5 داخلی غیرفعال شد.', 'en'), '5 extension(s) deactivated.')
        self.assertEqual(i18n.translate('اتاق 101', 'en'), 'Room 101')


class LanguageSwitchTests(AppTestCase):
    def page(self, client, path=''):
        return client.get(f'/{self.prefix}{path}').get_data(as_text=True)

    def test_default_is_persian_rtl(self):
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('lang="fa" dir="rtl"', html)

    def test_switch_sets_cookie_and_renders_english_ltr(self):
        resp = self.anon.get('/lang/en', headers={'Referer': 'http://localhost/?q=x'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('hp_lang=en', resp.headers.get('Set-Cookie', ''))
        self.assertEqual(resp.headers['Location'], '/?q=x')
        html = self.anon.get('/').get_data(as_text=True)
        self.assertIn('lang="en" dir="ltr"', html)
        self.assertIn('Hotel Araz', html)
        self.assertIn('window.HP_I18N', html)

    def test_redirect_never_leaves_the_site(self):
        for referer in ('http://evil.example/x', 'http://localhost//evil.example'):
            resp = self.anon.get('/lang/fa', headers={'Referer': referer})
            self.assertEqual(resp.headers['Location'], '/')

    def test_unknown_language_is_404(self):
        self.assertEqual(self.anon.get('/lang/de').status_code, 404)

    def test_admin_sidebar_in_both_languages(self):
        fa = self.page(self.admin)
        self.assertIn('class="sidebar', fa)
        self.assertIn('لیست داخلی‌ها', fa)
        self.admin.get('/lang/en')
        en = self.page(self.admin)
        self.assertIn('Extension list', en)
        self.assertNotIn('لیست داخلی‌ها', en)
        self.assertIn('<title>Manage Extensions | Hotel Araz</title>', en)

    def test_stored_values_are_not_translated_inside_inputs(self):
        self.admin.get('/lang/en')
        html = self.page(self.admin)
        # seeded department names are shown translated, but the editable input keeps the stored text
        self.assertRegex(html, r'class="[^"]*dept-chip-input[^"]*"[^>]*value="پذیرش"|value="پذیرش"[^>]*dept-chip-input')
        self.assertIn('>Reception<', html)

    def test_search_box_keeps_what_was_typed(self):
        self.admin.get('/lang/en')
        html = self.admin.get(f'/{self.prefix}', query_string={'q': 'پذیرش'}).get_data(as_text=True)
        self.assertIn('value="پذیرش"', html)

    def test_flash_messages_are_translated(self):
        self.admin.get('/lang/en')
        resp = self.admin.post(f'/{self.prefix}/departments', data={'name': ''}, follow_redirects=True)
        self.assertNotRegex(resp.get_data(as_text=True).split('id="flashSeeds"')[-1][:400], '[\u0600-\u06FF]')

    def test_clear_all_accepts_the_english_word(self):
        self.add('300', 'x')
        self.admin.post(f'/{self.prefix}/extensions/delete-all', data={'confirmation': 'clear'})
        self.assertTrue(self.get_ext('300') is None or self.get_ext('300')['is_deleted'])


if __name__ == '__main__':
    unittest.main()
