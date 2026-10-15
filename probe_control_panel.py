"""Look at what the Issabel Operator Panel's status stream (action=pbxStatus) sends, without the app.

    set PBX_WEB_HOST=https://192.168.1.100 PBX_WEB_USER=monitor PBX_WEB_PASS=...   (PowerShell: $env:NAME="...")
    python tools/probe_control_panel.py                # open the stream like the browser does, read 2 messages, close it
    python tools/probe_control_panel.py --blank-hash   # same, but tell the server we hold no state yet
    python tools/probe_control_panel.py --save raw.txt # also keep the raw bytes (may contain call numbers: do not share as is)

Read-only: it logs in, opens the same status stream the panel page opens, reads for a few seconds,
then closes it and sends the panel's own "pbxStatusShutdown" so no listener is left on the PBX.
It never calls callExtension / hangupExtension / voicemailExtension or any panel-changing action.
What it prints is a structure summary; digit runs of 7+ (outside numbers) are masked.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import requests  # noqa: E402

from pbx_sync import AMIError, IssabelWebScraper  # noqa: E402

PANEL = '/index.php?menu=control_panel'
HASH_RE = re.compile(r'"ESTADO_CLIENTE_HASH"\s*:\s*"([^"]*)"')
LONG_DIGITS = re.compile(r'\d{7,}')
READ_SECONDS, MAX_BYTES, MAX_EVENTS = 15, 2_000_000, 2


def mask(text):
    return LONG_DIGITS.sub('<number>', str(text))


def describe(value, depth=0, max_depth=6):
    pad = '  ' * depth
    if depth > max_depth:
        return [pad + '...']
    lines = []
    if isinstance(value, dict):
        lines.append(f'{pad}object with {len(value)} keys')
        for key in list(value)[:14]:
            child = value[key]
            if isinstance(child, (dict, list)):
                lines.append(f'{pad}  {key}:')
                lines.extend(describe(child, depth + 2, max_depth))
            else:
                lines.append(f'{pad}  {key}: {mask(repr(child))[:70]}')
        if len(value) > 14:
            lines.append(f'{pad}  ... {len(value) - 14} more keys')
    elif isinstance(value, list):
        lines.append(f'{pad}list of {len(value)}')
        for item in value[:2]:
            lines.extend(describe(item, depth + 1, max_depth))
    else:
        lines.append(f'{pad}{mask(repr(value))[:70]}')
    return lines


def split_events(buffer):
    """bytes -> (complete SSE events as text, rest). Events end with a blank line."""
    events = []
    while True:
        match = re.search(rb'\r?\n\r?\n', buffer)
        if not match:
            return events, buffer
        events.append(buffer[:match.start()].decode('utf-8', 'replace'))
        buffer = buffer[match.end():]


def payload_of(event):
    data = [line[5:].lstrip() for line in event.splitlines() if line.startswith('data:')]
    return '\n'.join(data) if data else event


def main():
    host, user, password = os.getenv('PBX_WEB_HOST', ''), os.getenv('PBX_WEB_USER', ''), os.getenv('PBX_WEB_PASS', '')
    if not (host and user and password):
        sys.exit('Set PBX_WEB_HOST, PBX_WEB_USER and PBX_WEB_PASS first.')
    verify = os.getenv('PBX_VERIFY_SSL', '').strip().lower() in ('1', 'true', 'yes', 'on')
    scraper = IssabelWebScraper(host, user, password, verify, 10.0, peers_path=PANEL)
    try:
        page = scraper.fetch_page()
    except AMIError as exc:
        sys.exit(f'FAILED: {exc}')
    found = HASH_RE.search(page)
    state_hash = '' if '--blank-hash' in sys.argv or not found else found.group(1)
    print(f'Logged in; panel page {len(page)} bytes; client state hash: {"(blank)" if not state_hash else "from the page"}')

    params = {'menu': 'control_panel', 'rawmode': 'yes', 'clientstatehash': state_hash, 'action': 'pbxStatus', 'serverevents': 'true'}
    received = bytearray()
    events, response = [], None
    try:
        response = scraper.session.get(scraper.base_url + '/index.php', params=params, stream=True, timeout=(5, 8),
                                       verify=scraper.verify_ssl, headers={'Accept': 'text/event-stream'})
        print(f'Stream answered HTTP {response.status_code}, {response.headers.get("Content-Type", "?")}')
        deadline, pending = time.monotonic() + READ_SECONDS, b''
        for chunk in response.iter_content(chunk_size=1):
            received += chunk
            pending += chunk
            ready, pending = split_events(pending)
            events.extend(ready)
            if len(events) >= MAX_EVENTS or len(received) > MAX_BYTES or time.monotonic() > deadline:
                break
        if not events and pending.strip():
            events.append(pending.decode('utf-8', 'replace'))  # a plain (non-SSE) body without a closing blank line
    except requests.RequestException as exc:
        print(f'Stream stopped: {exc.__class__.__name__} ({str(exc)[:120]})')
    finally:
        if response is not None:
            response.close()
        try:  # the panel itself does this when its window closes
            reply = scraper.session.post(scraper.base_url + '/index.php?menu=control_panel&rawmode=yes',
                                         data={'menu': 'control_panel', 'rawmode': 'yes', 'action': 'pbxStatusShutdown'},
                                         timeout=8, verify=scraper.verify_ssl)
            print(f'pbxStatusShutdown: HTTP {reply.status_code} {mask(reply.text[:120])!r}')
        except requests.RequestException as exc:
            print(f'pbxStatusShutdown failed: {exc.__class__.__name__}')

    if '--save' in sys.argv and sys.argv.index('--save') + 1 < len(sys.argv):
        with open(sys.argv[sys.argv.index('--save') + 1], 'wb') as handle:
            handle.write(bytes(received))
        print('Raw bytes saved (they may contain call numbers; do not share the file as is).')
    print(f'\n{len(events)} message(s), {len(received)} bytes received in total')
    for number, event in enumerate(events, 1):
        body = payload_of(event)
        print(f'--- message {number}: {len(body)} characters')
        try:
            print('\n'.join(describe(json.loads(body))))
        except ValueError:
            print('(not JSON) ' + mask(body[:300]))


if __name__ == '__main__':
    main()
