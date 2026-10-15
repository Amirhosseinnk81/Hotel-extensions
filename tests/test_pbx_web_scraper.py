"""Tests for the Issabel web-panel source (login, session handling, `sip show peers` parsing).

No Flask needed: a small fake Issabel web server speaks plain HTTP on localhost. The sample rows
are real `sip show peers` output from the hotel PBX plus a few special cases.
"""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pbx_sync
from pbx_sync import IssabelWebScraper, PBXWebError, PBXWebLoginRejected, page_to_text, parse_peer_listing

SIP_SHOW_PEERS = """\
Name/username             Host                                    Dyn Forcerport Comedia    ACL Port     Status      Description
100/100                   192.168.225.66                           D  Yes        No             5060     OK (3 ms)
101/101                   192.168.231.247                          D  Yes        No             5060     OK (5 ms)
1003/1003                 192.168.224.77                           D  Yes        No             5060     LAGGED (210 ms)
1004/1004                 (Unspecified)                            D  Yes        No             0        UNKNOWN
1005/1005                 192.168.225.131                          D  Yes        No             5060     UNREACHABLE
2001/2001                 192.168.226.9                            D  Yes        No         A   5062     Unmonitored
777                       192.168.226.10                           D  Yes        No             5060     OK (6 ms)    Guest phone
main-trunk/main-trunk     10.0.0.9                                 N  Yes        No             5060     OK (20 ms)
8 sip peers [Monitored: 5 online, 2 offline Unmonitored: 1 online, 0 offline]
"""


def as_pre(text):
    return f'<html><body><h2>SIP Peers</h2><pre>{text}</pre></body></html>'


def as_table(text):
    rows = []
    for line in text.splitlines()[1:-1]:
        cells = line.split()
        # the Description column is optional, the host may be "(Unspecified)": keep every token in its own cell
        rows.append('<tr>' + ''.join(f'<td>{c}</td>' for c in cells[:2]) + '<td></td><td>' + ' '.join(cells[2:-2]) + '</td><td>' + ' '.join(cells[-2:]) + '</td></tr>')
    return '<html><body><table><tr><th>Name</th><th>Host</th></tr>' + ''.join(rows) + '</table></body></html>'


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.peers = parse_peer_listing(SIP_SHOW_PEERS)

    def test_every_peer_row_is_found(self):
        self.assertEqual(sorted(self.peers), ['100', '1003', '1004', '1005', '101', '2001', '777', 'main-trunk'])

    def test_online_with_latency(self):
        peer = self.peers['101']
        self.assertEqual((peer.state, peer.rtt_ms, peer.ip, peer.port, peer.status_raw), ('online', 5, '192.168.231.247', 5060, 'OK (5 ms)'))

    def test_lagged_is_online_but_slow(self):
        self.assertEqual((self.peers['1003'].state, self.peers['1003'].rtt_ms), ('online', 210))

    def test_unreachable_is_offline(self):
        self.assertEqual((self.peers['1005'].state, self.peers['1005'].rtt_ms), ('offline', None))

    def test_unspecified_host_means_not_registered(self):
        peer = self.peers['1004']
        self.assertEqual((peer.state, peer.ip), ('offline', ''))

    def test_unmonitored_with_an_address_is_online_without_latency(self):
        peer = self.peers['2001']
        self.assertEqual((peer.state, peer.rtt_ms, peer.port), ('online', None, 5062))

    def test_row_without_username_part_and_with_description(self):
        self.assertEqual((self.peers['777'].state, self.peers['777'].rtt_ms), ('online', 6))

    def test_header_and_summary_lines_are_not_peers(self):
        self.assertNotIn('Name', self.peers)
        self.assertNotIn('8', self.peers)

    def test_html_pre_and_html_table_give_the_same_result(self):
        for markup in (as_pre(SIP_SHOW_PEERS), as_table(SIP_SHOW_PEERS)):
            parsed = parse_peer_listing(page_to_text(markup))
            self.assertEqual({n: (p.state, p.rtt_ms, p.ip) for n, p in parsed.items()},
                             {n: (p.state, p.rtt_ms, p.ip) for n, p in self.peers.items()}, markup[:40])

    def test_pjsip_endpoints(self):
        text = ' Endpoint:  100/100                                         Not in use    0 of inf\n' \
               ' Endpoint:  101/101                                         Unavailable   0 of inf\n' \
               ' Endpoint:  102/102                                         In use        1 of inf\n' \
               ' Endpoint:  103/103                                         Invalid       0 of inf\n' \
               ' Endpoint:  <Endpoint/CID.....................................>  <State.....>  <Channels.>\n'
        states = {n: p.state for n, p in parse_peer_listing(text).items()}
        self.assertEqual(states, {'100': 'online', '101': 'offline', '102': 'online', '103': 'unknown'})

    def test_garbage_gives_nothing(self):
        self.assertEqual(parse_peer_listing(''), {})
        self.assertEqual(parse_peer_listing(None), {})
        self.assertEqual(parse_peer_listing('<html>Access denied</html>'), {})
        self.assertEqual(parse_peer_listing(page_to_text('<html><script>var x = "100 1.2.3.4 5060 OK (3 ms)"</script></html>')), {})


class FakeIssabel(ThreadingHTTPServer):
    """Login form on /index.php, peers on /index.php?menu=asterisk_info&rawmode=yes."""

    def __init__(self, user='admin', password='right-pass', page=None):
        self.user, self.password = user, password
        self.page = page if page is not None else as_pre(SIP_SHOW_PEERS)
        self.sessions, self.logins, self.page_requests, self.failed_logins = set(), 0, 0, 0
        self.peers_status = 200
        self.csrf_seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, cookie=None):
                data = body.encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(data)))
                if cookie:
                    self.send_header('Set-Cookie', cookie)
                self.end_headers()
                self.wfile.write(data)

            def _session(self):
                for part in self.headers.get('Cookie', '').split(';'):
                    name, _, value = part.strip().partition('=')
                    if name == 'issabelSession' and value in outer.sessions:
                        return value
                return None

            def do_GET(self):
                if self.path.startswith('/index.php?menu=asterisk_info'):
                    outer.page_requests += 1
                    if outer.peers_status != 200:
                        return self._send(outer.peers_status, 'nope')
                    if not self._session():
                        return self._send(200, '<form><input type="text" name="input_user"><input type="password" name="input_pass"></form>')
                    return self._send(200, outer.page)
                return self._send(200, '<form method="post"><input type="hidden" name="csrf" value="tok123">'
                                       '<input type="text" name="input_user"><input type="password" name="input_pass"></form>',
                                  cookie='issabelSession=anon; Path=/')

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                outer.csrf_seen.append(form.get('csrf', [''])[0])
                if form.get('input_user') == [outer.user] and form.get('input_pass') == [outer.password]:
                    outer.logins += 1
                    token = f'sess{outer.logins}'
                    outer.sessions.add(token)
                    return self._send(200, '<html>Issabel Dashboard</html>', cookie=f'issabelSession={token}; Path=/')
                outer.failed_logins += 1
                return self._send(200, '<form><div>Incorrect username or password</div><input type="password" name="input_pass"></form>')

        super().__init__(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_address[1]}'

    def stop(self):
        self.shutdown()
        self.server_close()


class ScraperTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeIssabel()
        self.addCleanup(self.server.stop)

    def scraper(self, **kw):
        kw.setdefault('password', 'right-pass')
        kw.setdefault('peers_path', '/index.php?menu=asterisk_info&rawmode=yes')  # the default is the Operator Panel now
        return IssabelWebScraper(self.server.url, 'admin', timeout=3, **kw)

    def test_login_and_peer_states(self):
        peers = self.scraper().fetch_peer_states()
        self.assertEqual(peers['101'].state, 'online')
        self.assertEqual(self.server.logins, 1)
        self.assertEqual(self.server.csrf_seen, ['tok123'])  # hidden form fields are echoed back

    def test_spec_shaped_status_dictionary(self):
        statuses = self.scraper().get_peer_statuses()
        self.assertEqual(statuses['101'], {'status': 'online', 'latency': '5ms', 'raw_status': 'OK (5 ms)', 'ip': '192.168.231.247'})
        self.assertEqual(statuses['1005']['status'], 'offline')
        self.assertIsNone(statuses['1005']['latency'])

    def test_session_is_reused_between_cycles(self):
        scraper = self.scraper()
        scraper.fetch_peer_states()
        scraper.fetch_peer_states()
        self.assertEqual(self.server.logins, 1)
        self.assertEqual(self.server.page_requests, 2)

    def test_expired_session_logs_in_again(self):
        scraper = self.scraper()
        scraper.fetch_peer_states()
        self.server.sessions.clear()  # the panel forgot us
        self.assertEqual(scraper.fetch_peer_states()['100'].state, 'online')
        self.assertEqual(self.server.logins, 2)

    def test_wrong_password_is_reported_without_leaking_it(self):
        scraper = self.scraper(password='wrong-pass-123')
        with self.assertRaises(PBXWebLoginRejected) as ctx:
            scraper.fetch_peer_states()
        self.assertNotIn('wrong-pass-123', str(ctx.exception))
        self.assertIn('user name / password', str(ctx.exception))

    def test_wrong_password_is_not_retried_in_a_loop(self):
        scraper = self.scraper(password='wrong-pass-123')
        for _ in range(3):
            with self.assertRaises(PBXWebLoginRejected):
                scraper.fetch_peer_states()
        self.assertEqual(self.server.failed_logins, 1)  # cool-down: the panel saw a single attempt

    def test_cool_down_ends(self):
        scraper = self.scraper(password='wrong-pass-123')
        with self.assertRaises(PBXWebLoginRejected):
            scraper.fetch_peer_states()
        scraper._rejected_at -= pbx_sync.LOGIN_RETRY_SECONDS + 1
        with self.assertRaises(PBXWebLoginRejected):
            scraper.fetch_peer_states()
        self.assertEqual(self.server.failed_logins, 2)

    def test_unreachable_panel(self):
        server_url = self.server.url
        self.server.stop()
        scraper = IssabelWebScraper(server_url, 'admin', 'right-pass', timeout=2)
        with self.assertRaises(PBXWebError) as ctx:
            scraper.fetch_peer_states()
        self.assertIn('cannot reach', str(ctx.exception))
        self.assertNotIn('right-pass', str(ctx.exception))
        self.server = FakeIssabel()  # for the tearDown stop()

    def test_page_without_peers_is_an_error_not_an_empty_list(self):
        self.server.page = '<html>Access denied for this module</html>'
        with self.assertRaises(PBXWebError) as ctx:
            self.scraper().fetch_peer_states()
        self.assertIn('no SIP peers', str(ctx.exception))

    def test_server_error_page(self):
        scraper = self.scraper()
        scraper.fetch_peer_states()
        self.server.peers_status = 500
        with self.assertRaises(PBXWebError) as ctx:
            scraper.fetch_peer_states()
        self.assertIn('HTTP 500', str(ctx.exception))

    def test_table_layout_is_read_too(self):
        self.server.page = as_table(SIP_SHOW_PEERS)
        peers = self.scraper().fetch_peer_states()
        self.assertEqual((peers['1003'].state, peers['1003'].rtt_ms), ('online', 210))

    def test_host_without_scheme_and_bad_hosts(self):
        self.assertEqual(IssabelWebScraper('192.168.1.100', 'a', 'b').base_url, 'https://192.168.1.100')
        self.assertEqual(IssabelWebScraper('https://pbx.local/', 'a', 'b').base_url, 'https://pbx.local')
        with self.assertRaises(PBXWebError):
            IssabelWebScraper('ftp://pbx.local', 'a', 'b')

    def test_shared_scraper_reuses_the_session_per_configuration(self):
        a = pbx_sync.shared_scraper(self.server.url, 'admin', 'right-pass')
        self.assertIs(a, pbx_sync.shared_scraper(self.server.url, 'admin', 'right-pass'))
        self.assertIsNot(a, pbx_sync.shared_scraper(self.server.url, 'admin', 'other'))


if __name__ == '__main__':
    unittest.main()