"""Try the Issabel web-panel source by hand, without starting the app.

    set PBX_WEB_HOST=https://192.168.1.100 PBX_WEB_USER=monitor PBX_WEB_PASS=...   (PowerShell: $env:NAME="...")
    python tools/check_issabel_web.py            # log in, read the peers page, print what was understood
    python tools/check_issabel_web.py --raw      # also print the start of the page text, to adapt PBX_WEB_PEERS_PATH
    python tools/check_issabel_web.py --save page.html   # write the page's raw HTML (scripts included) to a file

Read-only: it only logs in and opens one page. The password is never printed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from pbx_sync import AMIError, DEFAULT_LOGIN_PATH, DEFAULT_PEERS_PATH, IssabelWebScraper, page_to_text  # noqa: E402


def main():
    host, user, password = os.getenv('PBX_WEB_HOST', ''), os.getenv('PBX_WEB_USER', ''), os.getenv('PBX_WEB_PASS', '')
    if not (host and user and password):
        sys.exit('Set PBX_WEB_HOST, PBX_WEB_USER and PBX_WEB_PASS first.')
    verify = os.getenv('PBX_VERIFY_SSL', '').strip().lower() in ('1', 'true', 'yes', 'on')
    scraper = IssabelWebScraper(host, user, password, verify, float(os.getenv('PBX_WEB_TIMEOUT', '10') or 10),
                                os.getenv('PBX_WEB_LOGIN_PATH') or DEFAULT_LOGIN_PATH, os.getenv('PBX_WEB_PEERS_PATH') or DEFAULT_PEERS_PATH)
    try:
        page = scraper.fetch_page()
    except AMIError as exc:
        sys.exit(f'FAILED: {exc}')
    print(f'Logged in to {scraper.base_url} and opened {scraper.peers_path} ({len(page)} bytes)')
    if '--save' in sys.argv:
        target = sys.argv[sys.argv.index('--save') + 1] if sys.argv.index('--save') + 1 < len(sys.argv) else 'issabel_page.html'
        with open(target, 'w', encoding='utf-8') as handle:
            handle.write(page)
        print(f'Raw HTML saved to {target} (it contains no password; check it for anything private before sharing)')
    if '--raw' in sys.argv:
        print('--- start of the page text ---')
        print(page_to_text(page)[:3000])
        print('--- end ---')
    try:
        peers = scraper.fetch_peer_states()
    except AMIError as exc:
        sys.exit(f'FAILED: {exc}')
    counts = {}
    for peer in peers.values():
        counts[peer.state] = counts.get(peer.state, 0) + 1
    print(f'{len(peers)} peers understood: ' + ', '.join(f'{n} {s}' for s, n in sorted(counts.items())))
    for name in sorted(peers, key=lambda n: (len(n), n))[:15]:
        peer = peers[name]
        print(f'  {name:<12} {peer.state:<8} {peer.status_raw:<16} {peer.ip}')
    if len(peers) > 15:
        print(f'  ... and {len(peers) - 15} more')


if __name__ == '__main__':
    main()