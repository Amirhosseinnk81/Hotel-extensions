"""Tests for the Operator Panel source (menu=control_panel status stream).

Message layout is taken from the real hotel PBX: one JSON object {"pbxchanges": [...]} whose
`phones` entries carry extension, channel, ip and registered. A small fake Issabel server on
localhost serves the login form, the panel page (with the client state hash) and the stream.
"""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from pbx_sync import (IssabelWebScraper, JsonObjectScanner, PBXWebError, parse_panel_changes)


def phone(ext, ip='', registered=True, changetype='create', objtype='phones', **extra):
    item = {'channel': f'SIP/{ext}', 'tech': 'sip', 'extension': ext, 'description': ext, 'current_area': 'Extension',
            'mailbox': f'{ext}@default', 'UrgMessages': 0, 'NewMessages': 0, 'OldMessages': 0, 'ip': ip,
            'registered': registered, 'active': [], 'objtype': objtype, 'changetype': changetype}
    item.update(extra)
    return item


MESSAGE = {'pbxchanges': [
    phone('100', '192.168.225.66'),
    phone('101', '192.168.231.247'),
    phone('1005', '', registered=False),
    phone('2001', '-none-', registered=False),
    {'extension': '8000', 'description': 'Conf', 'objtype': 'conferences', 'changetype': 'create'},
    {'channel': 'SIP/main-trunk', 'objtype': 'iptrunks', 'changetype': 'create'},
], 'timestamp': 1792101990, 'estadoClienteHash': 'newhash'}


class ParsePanelTests(unittest.TestCase):
    def test_only_phones_are_read(self):
        self.assertEqual(sorted(parse_panel_changes(MESSAGE)), ['100', '1005', '101', '2001'])

    def test_registered_is_online_without_latency(self):
        peer = parse_panel_changes(MESSAGE)['100']
        self.assertEqual((peer.state, peer.ip, peer.rtt_ms, peer.status_raw), ('online', '192.168.225.66', None, 'Registered'))

    def test_not_registered_is_offline_and_placeholder_ip_is_dropped(self):
        peers = parse_panel_changes(MESSAGE)
        self.assertEqual((peers['1005'].state, peers['1005'].ip, peers['1005'].status_raw), ('offline', '', 'Not registered'))
        self.assertEqual(peers['2001'].ip, '')

    def test_update_after_create_wins_and_delete_is_ignored(self):
        peers = parse_panel_changes({'pbxchanges': [phone('100', '1.2.3.4'), phone('100', '', registered=False, changetype='update'),
                                                    phone('101', '1.2.3.5', changetype='delete')]})
        self.assertEqual(sorted(peers), ['100'])
        self.assertEqual(peers['100'].state, 'offline')

    def test_missing_registered_flag_is_unknown(self):
        item = phone('100')
        del item['registered']
        self.assertEqual(parse_panel_changes({'pbxchanges': [item]})['100'].state, 'unknown')

    def test_name_falls_back_to_the_channel(self):
        self.assertIn('555', parse_panel_changes({'pbxchanges': [phone('', '1.1.1.1', channel='SIP/555')]}))

    def test_garbage_gives_nothing(self):
        for value in (None, [], {}, {'pbxchanges': None}, {'pbxchanges': ['x', 3]}):
            self.assertEqual(parse_panel_changes(value), {})


class ScannerTests(unittest.TestCase):
    def feed_all(self, raw, size):
        scanner, result = JsonObjectScanner(), None
        for i in range(0, len(raw), size):
            result = scanner.feed(raw[i:i + size]) or result
        return result

    def test_plain_json_any_chunk_size(self):
        raw = json.dumps(MESSAGE).encode()
        for size in (1, 7, 4096, len(raw)):
            self.assertEqual(self.feed_all(raw, size), MESSAGE)

    def test_sse_framing_is_ignored(self):
        raw = b'retry: 3000\n\ndata: ' + json.dumps(MESSAGE).encode() + b'\n\n'
        self.assertEqual(self.feed_all(raw, 5), MESSAGE)

    def test_braces_and_quotes_inside_strings(self):
        message = {'pbxchanges': [], 'x': 'a } { \\" ", "y": {', 'name': 'ال\u200fف'}
        self.assertEqual(self.feed_all(json.dumps(message, ensure_ascii=False).encode('utf-8'), 3), message)

    def test_truncated_object_gives_nothing(self):
        self.assertIsNone(self.feed_all(json.dumps(MESSAGE).encode()[:-5], 10))

    def test_invalid_json_is_an_error(self):
        with self.assertRaises(PBXWebError):
            JsonObjectScanner().feed(b'{"a": nope}')

    def test_head_is_kept_for_login_page_detection(self):
        scanner = JsonObjectScanner()
        scanner.feed(b'<form><input name="input_pass"></form>')
        self.assertIn(b'input_pass', scanner.head)


class FakePanel(ThreadingHTTPServer):
    """Login on /index.php, panel page with a state hash, status stream, shutdown call."""

    def __init__(self, message=MESSAGE, framing='sse', user='admin', password='pw'):
        self.message, self.framing, self.user, self.password = message, framing, user, password
        self.sessions, self.logins, self.streams, self.shutdowns, self.hashes = set(), 0, 0, 0, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body, cookie=None, status=200):
                data = body.encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                if cookie:
                    self.send_header('Set-Cookie', cookie)
                self.end_headers()
                self.wfile.write(data)

            def _logged_in(self):
                return any(p.strip().partition('=')[2] in outer.sessions for p in self.headers.get('Cookie', '').split(';'))

            def do_GET(self):
                query = parse_qs(urlsplit(self.path).query)
                if query.get('menu') == ['control_panel'] and not self._logged_in():
                    return self._send('<form><input name="input_user"><input type="password" name="input_pass"></form>')
                if query.get('action') == ['pbxStatus']:
                    outer.streams += 1
                    outer.hashes.append(query.get('clientstatehash', [None])[0])
                    body = json.dumps(outer.message)
                    payload = ('data: ' + body + '\n\n') if outer.framing == 'sse' else body
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    self.wfile.write(payload.encode())
                    self.wfile.flush()
                    time.sleep(0.3)  # like the real stream: stays open after the first message
                    return
                if query.get('menu') == ['control_panel']:
                    return self._send('<html><script>var var_init = {"ESTADO_CLIENTE_HASH":"abc123","ESTADO_PANELES":{}};</script></html>')
                return self._send('<form><input name="input_user"><input type="password" name="input_pass"></form>', cookie='sid=anon; Path=/')

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                if form.get('action') == ['pbxStatusShutdown']:
                    outer.shutdowns += 1
                    return self._send('{"status":"success","message":"(no message)"}')
                if form.get('input_user') == [outer.user] and form.get('input_pass') == [outer.password]:
                    outer.logins += 1
                    token = f'sess{outer.logins}'
                    outer.sessions.add(token)
                    return self._send('<html>Dashboard</html>', cookie=f'sid={token}; Path=/')
                return self._send('<form><div>Incorrect username or password</div><input type="password" name="input_pass"></form>')

        super().__init__(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_address[1]}'

    def stop(self):
        self.shutdown()
        self.server_close()


class PanelScraperTests(unittest.TestCase):
    def make(self, **kw):
        server = FakePanel(**kw)
        self.addCleanup(server.stop)
        return server, IssabelWebScraper(server.url, 'admin', 'pw', timeout=3)  # default path = Operator Panel

    def test_default_source_is_the_operator_panel(self):
        _, scraper = self.make()
        self.assertIn('control_panel', scraper.peers_path)

    def test_states_hash_and_shutdown(self):
        server, scraper = self.make()
        peers = scraper.fetch_peer_states()
        self.assertEqual((peers['100'].state, peers['1005'].state), ('online', 'offline'))
        self.assertEqual(server.hashes, ['abc123'])   # the hash from the panel page is sent back
        self.assertEqual(server.shutdowns, 1)          # no listener is left on the PBX

    def test_spec_shaped_dictionary(self):
        _, scraper = self.make()
        self.assertEqual(scraper.get_peer_statuses()['101'],
                         {'status': 'online', 'latency': None, 'raw_status': 'Registered', 'ip': '192.168.231.247'})

    def test_plain_json_without_sse_framing(self):
        _, scraper = self.make(framing='plain')
        self.assertEqual(len(scraper.fetch_peer_states()), 4)

    def test_session_is_reused_and_relogin_after_expiry(self):
        server, scraper = self.make()
        scraper.fetch_peer_states()
        scraper.fetch_peer_states()
        self.assertEqual(server.logins, 1)
        server.sessions.clear()
        self.assertEqual(len(scraper.fetch_peer_states()), 4)
        self.assertEqual(server.logins, 2)

    def test_message_without_phones_is_an_error_not_an_empty_list(self):
        _, scraper = self.make(message={'pbxchanges': [], 'estadoClienteHash': 'x'})
        with self.assertRaises(PBXWebError) as ctx:
            scraper.fetch_peer_states()
        self.assertIn('no extensions', str(ctx.exception))

    def test_panel_error_message(self):
        _, scraper = self.make(message={'error': 'AMI connection lost'})
        with self.assertRaises(PBXWebError) as ctx:
            scraper.fetch_peer_states()
        self.assertIn('AMI connection lost', str(ctx.exception))

    def test_wrong_password_never_leaks(self):
        server = FakePanel()
        self.addCleanup(server.stop)
        scraper = IssabelWebScraper(server.url, 'admin', 'secret-wrong', timeout=3)
        with self.assertRaises(PBXWebError) as ctx:
            scraper.fetch_peer_states()
        self.assertNotIn('secret-wrong', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()