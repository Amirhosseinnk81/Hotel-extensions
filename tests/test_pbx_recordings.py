"""Call recordings (pbx_recordings.py, IssabelWebScraper.open_stream and the /recordings pages).

The CDR and Issabel are fakes: the CDR answers canned rows, the panel answers a fake streaming response.
Checked: path traversal is refused before anything reaches Issabel, only allowed roles get audio (403
otherwise), every playback / download is logged with user, IP and masked numbers, downloads are rate
limited, nothing is written to disk, and there is no route that could delete or change a recording.
"""
import io
import os
import unittest
from datetime import datetime
from unittest import mock

import app as m
import features
import pbx_cdr
import pbx_recordings as pr
from pbx_connectors import ConnectorError
from pbx_sync import IssabelWebScraper, PBXWebError
from tests.test_rbac import LEGACY_PASSWORD, AppCase

UID = '1762580100.12'
ROW = {'calldate': datetime(2026, 11, 8, 9, 15), 'src': '09121234567', 'dst': '101', 'billsec': 65, 'uniqueid': UID,
       'recordingfile': 'external-101-09121234567-20261108-091500-1762580100.12.wav'}
AUDIO = b'RIFF' + b'\x00' * 70000


class NameTests(unittest.TestCase):
    def test_accepted(self):
        self.assertEqual(pr.safe_recording_name('out-101-202-20261108-1.wav'), 'out-101-202-20261108-1.wav')
        self.assertEqual(pr.safe_recording_name('2026/11/08/q-600-1.WAV'), '2026/11/08/q-600-1.WAV')
        self.assertEqual(pr.safe_recording_name('/var/spool/asterisk/monitor/2026/11/08/x.gsm'), '2026/11/08/x.gsm')

    def test_traversal_and_junk_refused(self):
        for bad in ('../../etc/passwd', '/etc/passwd', '/var/spool/asterisk/monitor/../../../etc/shadow.wav',
                    '/var/spool/asterisk/monitorX/a.wav', 'a/b.wav', '2026/11/08/../../x.wav', '..wav', 'x.php', 'x.wav.php',
                    'x.wav\x00.php', '%2e%2e/x.wav', 'C:\\x.wav', '.hidden.wav', '', None, 'a b.wav', 'x;rm.wav'):
            self.assertIsNone(pr.safe_recording_name(bad), repr(bad))

    def test_uniqueid(self):
        self.assertTrue(pr.valid_uniqueid(UID))
        for bad in ('1', '1.', '.1', '1.1.1', '1/2', '1.1;', '../1.1', 12.5, None, '1' * 13 + '.1'):
            self.assertFalse(pr.valid_uniqueid(bad), repr(bad))

    def test_download_path_is_encoded_and_checked(self):
        path = pr.download_path(pr.DEFAULT_DOWNLOAD_PATH, UID, '2026/11/08/a+b.wav')
        self.assertEqual(path, '/index.php?menu=monitoring&action=download&id=1762580100.12&namefile=2026%2F11%2F08%2Fa%2Bb.wav&rawmode=yes')
        with self.assertRaises(ValueError):
            pr.download_path(pr.DEFAULT_DOWNLOAD_PATH, UID, '../x.wav')
        with self.assertRaises(ValueError):
            pr.download_path(pr.DEFAULT_DOWNLOAD_PATH, '1&menu=x', 'a.wav')

    def test_helpers(self):
        self.assertEqual(pr.mask_number('09121234567'), '0912*****67')
        self.assertEqual(pr.mask_number('101'), '101')
        self.assertEqual(pr.download_name('2026/11/08/a b"c.wav'), 'a_b_c.wav')
        self.assertEqual(pr.clean_range('bytes=0-'), 'bytes=0-')
        self.assertIsNone(pr.clean_range('bytes=0-1,5-9'))
        self.assertIsNone(pr.clean_range('bytes=-'))
        self.assertTrue(pr.starts_playback(None) and pr.starts_playback('bytes=0-'))
        self.assertFalse(pr.starts_playback('bytes=5000-'))

    def test_rate_limiter(self):
        now = [0.0]
        limiter = pr.RateLimiter(2, window=3600, clock=lambda: now[0])
        self.assertTrue(limiter.allow(1) and limiter.allow(1))
        self.assertFalse(limiter.allow(1))
        self.assertTrue(limiter.allow(2))
        now[0] = 3600
        self.assertTrue(limiter.allow(1))
        self.assertTrue(pr.RateLimiter(0).allow(1))


# ---------------------------------------------------------------------------- streaming client
class FakeRaw:
    def __init__(self, body):
        self.body = body

    def read(self, n, decode_content=False):
        return self.body[:n]


class FakeHttpResponse:
    def __init__(self, status=200, content_type='audio/x-wav', body=AUDIO, headers=None):
        self.status_code, self.body, self.closed = status, body, False
        self.headers = {'Content-Type': content_type, **(headers or {})}
        self.raw = FakeRaw(body)

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def close(self):
        self.closed = True


class OpenStreamTests(unittest.TestCase):
    def scraper(self, answers):
        scraper = IssabelWebScraper('https://10.0.0.1', 'u', 'p')
        scraper.logins = 0

        def login():
            scraper.logins += 1
            scraper._logged_in = True
            scraper.session = mock.Mock()
            scraper.session.get.side_effect = lambda *a, **k: answers.pop(0)
        scraper.login = login
        return scraper

    def test_audio_passes(self):
        response = self.scraper([FakeHttpResponse()]).open_stream('/index.php?x=1', {'Range': 'bytes=0-'})
        self.assertEqual(response.status_code, 200)

    def test_expired_session_logs_in_again_once(self):
        login_page = FakeHttpResponse(content_type='text/html', body=b'<form><input name="input_pass"></form>')
        scraper = self.scraper([login_page, FakeHttpResponse()])
        scraper.open_stream('/f')
        self.assertEqual(scraper.logins, 2)
        self.assertTrue(login_page.closed)

    def test_html_page_is_never_returned(self):
        with self.assertRaises(PBXWebError):
            self.scraper([FakeHttpResponse(content_type='text/html; charset=utf-8', body=b'<html>Error</html>')]).open_stream('/f')
        with self.assertRaises(PBXWebError):
            self.scraper([FakeHttpResponse(status=404, content_type='audio/wav')]).open_stream('/f')


# ---------------------------------------------------------------------------- pages
class FakeCdr:
    def __init__(self, row=ROW, fail=None):
        self.row, self.fail, self.queries = row, fail, []

    def select(self, sql, params=(), max_rows=1000):
        self.queries.append((sql, list(params)))
        if self.fail:
            raise self.fail
        if sql.startswith('SELECT COUNT(*)'):
            return [{'n': 1}]
        if sql == pbx_cdr.RECORDING_SQL:
            return [dict(self.row)] if self.row and params[0] == self.row['uniqueid'] else []
        return [{**self.row, 'dcontext': 'from-trunk', 'channel': 'SIP/t-1', 'dstchannel': 'SIP/101-2', 'lastapp': 'Dial',
                 'disposition': 'ANSWERED', 'duration': 70}]


class FakePanel:
    def __init__(self, response=None, fail=None):
        self.response, self.fail, self.calls = response, fail, []

    def open_stream(self, path, headers=None):
        self.calls.append((path, headers))
        if self.fail:
            raise self.fail
        return self.response or FakeHttpResponse(headers={'Content-Length': str(len(AUDIO)), 'Accept-Ranges': 'bytes'})


class RecordingCase(AppCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {'PBX_CDR_HOST': '10.0.0.2', 'PBX_CDR_USER': 'hotel_cdr', 'PBX_CDR_PASS': 'db-test-pw',
                                           'PBX_REC_DOWNLOADS_PER_HOUR': '2'})
        env.start()
        self.addCleanup(env.stop)
        self.cdr, self.panel = FakeCdr(), FakePanel()
        for name, value in (('make_cdr_client', lambda: self.cdr), ('recordings_scraper', lambda: self.panel)):
            patcher = mock.patch.object(m, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        m._rec_limiter.update(limit=None, limiter=None)
        self.start()
        self.prefix = self.app.config['ADMIN_URL_PREFIX']
        with self.app.app_context():
            roles = {r.key: r.id for r in m.Role.query.all()}
            listing = m.Role(key='listing', name='listing', permissions=[m.RolePermission(permission_key='recordings.view')])
            m.db.session.add(listing)
            m.db.session.flush()
            roles['listing'] = listing.id
            for name, role in (('boss', features.SUPER_ADMIN), ('op', 'operator'), ('lister', 'listing')):
                user = m.User(username=name, display_name=name, role_id=roles[role])
                user.set_password(LEGACY_PASSWORD)
                m.db.session.add(user)
            m.db.session.commit()

    def client(self, name='boss'):
        client = self.app.test_client()
        client.post(f'/{self.prefix}/login', data={'username': name, 'password': LEGACY_PASSWORD})
        return client

    def url(self, path=''):
        return f'/{self.prefix}/recordings{path}'

    def logs(self):
        with self.app.app_context():
            return [(l.action, l.actor, l.ip, l.entity_label, l.details) for l in m.ActivityLog.query.filter_by(entity_type='recording').all()]


class RecordingPageTests(RecordingCase):
    def test_list_with_player_and_notice(self):
        page = self.client().get(self.url('?range=today&number=0912')).get_data(as_text=True)
        self.assertIn(f'recordings/{UID}/play', page)
        self.assertIn(f'recordings/{UID}/download', page)
        self.assertIn('در لاگ فعالیت‌ها ثبت می‌شود', page)
        sql, params = self.cdr.queries[-1]
        self.assertIn("recordingfile <> ''", sql)
        self.assertIn('%0912%', params)
        self.assertEqual(self.panel.calls, [])  # listing never touches the audio

    def test_list_only_role_gets_no_player(self):
        client = self.client('lister')
        page = client.get(self.url()).get_data(as_text=True)
        self.assertNotIn('<audio', page)
        self.assertNotIn('/download', page)
        self.assertEqual(client.get(self.url(f'/{UID}/play')).status_code, 403)
        self.assertEqual(client.get(self.url(f'/{UID}/download')).status_code, 403)
        self.assertEqual(self.panel.calls, [])

    def test_operator_has_no_access_by_default(self):
        client = self.client('op')
        for path in ('', f'/{UID}/play', f'/{UID}/download'):
            self.assertEqual(client.get(self.url(path)).status_code, 403, path)

    def test_no_route_can_change_a_recording(self):
        rules = [r for r in self.app.url_map.iter_rules() if 'recordings' in r.rule]
        self.assertEqual({r.endpoint for r in rules}, {'recordings_list', 'recording_play', 'recording_download'})
        self.assertTrue(all(r.methods <= {'GET', 'HEAD', 'OPTIONS'} for r in rules))


class ProxyTests(RecordingCase):
    def test_play_streams_and_logs_once(self):
        client = self.client()
        response = client.get(self.url(f'/{UID}/play'), headers={'Range': 'bytes=0-'}, environ_base={'REMOTE_ADDR': '10.9.8.7'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, AUDIO)
        self.assertEqual(response.headers['Content-Type'], 'audio/x-wav')
        self.assertIn('inline', response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store, private')
        path, headers = self.panel.calls[0]
        self.assertTrue(path.startswith('/index.php?menu=monitoring&action=download&id=1762580100.12&namefile=external-101-'))
        self.assertEqual(headers, {'Range': 'bytes=0-'})
        client.get(self.url(f'/{UID}/play'), headers={'Range': 'bytes=40000-'})  # seeking inside the same playback
        logs = self.logs()
        self.assertEqual(len(logs), 1)
        action, actor, ip, label, details = logs[0]
        self.assertEqual((action, actor, ip, details), ('listened', 'boss', '10.9.8.7', f'uniqueid {UID}'))
        self.assertIn('0912*****67', label)
        self.assertNotIn('09121234567', label)

    def test_download_is_attachment_logged_and_limited(self):
        client = self.client()
        for _ in range(2):
            response = client.get(self.url(f'/{UID}/download'))
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment; filename="external-101-09121234567-20261108-091500-1762580100.12.wav"',
                          response.headers['Content-Disposition'])
            self.assertEqual(response.data, AUDIO)
        third = client.get(self.url(f'/{UID}/download'))
        self.assertEqual(third.status_code, 302)  # back to the list with a message
        self.assertIn('سقف دانلود', client.get(third.headers['Location']).get_data(as_text=True))
        self.assertEqual([l[0] for l in self.logs()], ['downloaded', 'downloaded'])
        self.assertEqual(len(self.panel.calls), 2)  # the refused one never reached Issabel

    def test_traversal_in_the_cdr_never_reaches_issabel(self):
        for bad in ('../../../../etc/passwd', '/etc/shadow', '/var/spool/asterisk/monitor/../../etc/x.wav', 'x.php'):
            self.cdr.row = {**ROW, 'recordingfile': bad}
            with self.assertLogs(self.app.logger, 'WARNING'):
                self.assertEqual(self.client().get(self.url(f'/{UID}/play')).status_code, 404, bad)
        self.assertEqual(self.panel.calls, [])
        self.assertEqual(self.logs(), [])

    def test_bad_or_unknown_ids(self):
        client = self.client()
        for uid in ('..%2F..%2Fetc', '1.1;DROP', 'abc'):
            self.assertEqual(client.get(self.url(f'/{uid}/play')).status_code, 404, uid)
        self.assertEqual(client.get(self.url('/999.1/play')).status_code, 404)  # no such recording in the CDR
        self.assertEqual(self.panel.calls, [])

    def test_issabel_errors(self):
        self.panel.fail = PBXWebError('Issabel answered with a web page instead of a file')
        with self.assertLogs(self.app.logger, 'WARNING'):
            response = self.client().get(self.url(f'/{UID}/play'))
        self.assertEqual(response.status_code, 502)
        self.panel.fail, self.panel.response = None, FakeHttpResponse(content_type='image/png')
        self.assertEqual(self.client().get(self.url(f'/{UID}/play')).status_code, 502)
        self.cdr.fail = ConnectorError('unreachable', 'cannot connect', 'cdr')
        with self.assertLogs(self.app.logger, 'WARNING'):
            self.assertEqual(self.client().get(self.url(f'/{UID}/play')).status_code, 502)
        self.assertEqual(self.logs(), [])  # nothing was heard, nothing is logged

    def test_octet_stream_gets_the_audio_type(self):
        self.panel.response = FakeHttpResponse(content_type='application/octet-stream')
        self.assertEqual(self.client().get(self.url(f'/{UID}/play')).headers['Content-Type'], 'audio/wav')

    def test_nothing_is_written_to_disk(self):
        before = {os.path.join(d, f) for d, _s, files in os.walk(self.tmp) for f in files}
        self.client().get(self.url(f'/{UID}/download'))
        after = {os.path.join(d, f) for d, _s, files in os.walk(self.tmp) for f in files}
        self.assertEqual({p for p in after - before if not p.endswith(('.db', '.db-journal', '-wal', '-shm'))}, set())


if __name__ == '__main__':
    unittest.main()
