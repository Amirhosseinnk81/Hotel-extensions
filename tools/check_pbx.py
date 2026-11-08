"""Check every PBX connection the app is configured for, without starting the app.

    python tools/check_pbx.py                  # AMI, CDR database and web panel: reachable? login accepted?
    python tools/check_pbx.py --dbget 50       # also time 50 AstDB reads (DBGet), to size the DND snapshot
    python tools/check_pbx.py --dashboard      # also show what was understood from the Issabel dashboard (health page)
    python tools/check_pbx.py --test-daynight-write   # the ONE writing check: writes HOTELTEST/probe (no dialplan reads it)
                                                      # with the day/night write user and method, then reads it back

Settings come from the .env file next to app.py (like the app itself). Read-only: it logs in, runs
CoreStatus / driver probes / SELECT VERSION() / the newest call time, and logs out. Secrets, phone
numbers and call details are never printed. Exit code 1 when a configured connection fails.
"""
import os
import re
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

import envfile  # noqa: E402

envfile.load_dotenv(envfile.env_path(os.path.abspath(ROOT)))

from pbx_connectors import AmiReader, AmiWriter, CdrClient, ConnectorError, load_settings  # noqa: E402
import pbx_daynight  # noqa: E402
import pbx_health  # noqa: E402
from pbx_sync import AMIError, DEFAULT_LOGIN_PATH, DEFAULT_PEERS_PATH, IssabelWebScraper, page_to_text  # noqa: E402

LONG_DIGITS = re.compile(r'\d{7,}')


def fail(name, exc):
    kind = getattr(exc, 'kind', 'error')
    print(f'  {name:<10} FAILED ({kind}): {exc}')
    fa = getattr(exc, 'fa', '')
    if fa:
        print(f'  {"":<10} {fa}')
    return False


def check_ami(settings, dbget):
    ami = settings.ami
    if not ami.ready:
        print('  AMI        not configured (PBX_AMI_HOST / PBX_AMI_USER / PBX_AMI_SECRET)')
        return True
    started = time.monotonic()
    try:
        with AmiReader(settings) as reader:
            login_ms = (time.monotonic() - started) * 1000
            status = reader.core_status()
            driver = reader.driver()
            print(f'  AMI        OK  {ami.host}:{ami.port} as {ami.user}, login {ami.auth} in {login_ms:.0f} ms')
            print(f'             driver: {driver}' + (' (detected)' if settings.sip_driver == 'auto' else ' (PBX_SIP_DRIVER)'))
            print(f"             Asterisk up since {status['startup'] or '?'}, last reload {status['reload'] or '?'}, "
                  f"{status['current_calls']} call(s) now")
            if dbget:
                begin = time.monotonic()
                found = reader.db_get_many('DND', [str(100 + i) for i in range(dbget)])
                per = (time.monotonic() - begin) * 1000 / dbget
                print(f'             DBGet: {dbget} reads in {per * dbget:.0f} ms ({per:.1f} ms each, {len(found)} key(s) exist); '
                      f'about {per * 4 / 1000:.2f} s per 1 extension x 4 families -> x your extension count')
    except ConnectorError as exc:
        return fail('AMI', exc)
    return True


def check_cdr(settings):
    cdr = settings.cdr
    if not cdr.ready:
        print('  CDR        not configured (PBX_CDR_HOST / PBX_CDR_USER / PBX_CDR_PASS)')
        return True
    try:
        info = CdrClient(settings).ping()
    except ConnectorError as exc:
        return fail('CDR', exc)
    print(f"  CDR        OK  {cdr.host}:{cdr.port}/{cdr.database} as {cdr.user}, server {info['version']}")
    print(f"             PBX database clock {info['now']}, newest call {info['latest_call'] or '(none)'}")
    return True


def show_dashboard(scraper):
    paths = [p.strip() for p in (os.getenv('PBX_WEB_HEALTH_PATHS') or '/index.php?menu=dashboard').split(',') if p.strip()]
    text = '\n'.join(page_to_text(scraper.fetch_path(p)) for p in paths)
    found = pbx_health.parse_dashboard(text)
    print(f'             dashboard {", ".join(paths)}: CPU {found["cpu"]}, memory {found["memory"]}, swap {found["swap"]}')
    print(f'             disks: {found["disks"] or "none found"}')
    print(f'             services: {found["services"] or "none found"}')
    if not pbx_health.has_values(found):
        print('             nothing understood; start of the page text (long numbers masked), to pick PBX_WEB_HEALTH_PATHS:')
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        print('\n'.join('               ' + LONG_DIGITS.sub('<number>', line)[:120] for line in lines[:40]))


def check_web(dashboard=False):
    host, user, password = os.getenv('PBX_WEB_HOST', ''), os.getenv('PBX_WEB_USER', ''), os.getenv('PBX_WEB_PASS', '')
    if not (host and user and password):
        print('  Web panel  not configured (PBX_WEB_HOST / PBX_WEB_USER / PBX_WEB_PASS)')
        return True
    verify = os.getenv('PBX_VERIFY_SSL', '').strip().lower() in ('1', 'true', 'yes', 'on')
    try:
        scraper = IssabelWebScraper(host, user, password, verify, float(os.getenv('PBX_WEB_TIMEOUT', '10') or 10),
                                    os.getenv('PBX_WEB_LOGIN_PATH') or DEFAULT_LOGIN_PATH,
                                    os.getenv('PBX_WEB_PEERS_PATH') or DEFAULT_PEERS_PATH)
        scraper.login()
    except AMIError as exc:
        return fail('Web panel', exc)
    print(f'  Web panel  OK  logged in to {scraper.base_url} as {user}')
    if dashboard:
        try:
            show_dashboard(scraper)
        except AMIError as exc:
            return fail('Dashboard', exc)
    return True


def test_daynight_write(settings):
    """Proves the write user and PBX_DAYNIGHT_WRITE_METHOD work, on a key nothing uses. Never touches DAYNIGHT/*."""
    method = (os.getenv('PBX_DAYNIGHT_WRITE_METHOD') or 'setvar').strip().lower()
    method = method if method in pbx_daynight.WRITE_METHODS else 'setvar'
    value = 'T' + str(int(time.time()))
    print(f'  Write test: {pbx_daynight.TEST_FAMILY}/{pbx_daynight.TEST_KEY} = {value} with method {method} as {settings.ami_write.user or "(no write user)"}')
    try:
        AmiWriter(settings).run(pbx_daynight.test_actions(method, value))
        with AmiReader(settings) as reader:
            found = reader.db_get(pbx_daynight.TEST_FAMILY, pbx_daynight.TEST_KEY)
    except ConnectorError as exc:
        return fail('Write test', exc)
    if found != value:
        print(f'  Write test FAILED: read back {found!r}; the write was accepted but did not change AstDB (live_dangerously?)')
        return False
    print('  Write test OK: written and read back. Remove the test key on the PBX with:')
    print(f'    asterisk -rx "database deltree {pbx_daynight.TEST_FAMILY}"')
    return True


def main(argv):
    dbget = 0
    if '--dbget' in argv:
        index = argv.index('--dbget')
        try:
            dbget = max(1, min(500, int(argv[index + 1])))
        except (IndexError, ValueError):
            sys.exit('usage: --dbget <count 1..500>')
    settings = load_settings()
    print('PBX connections:')
    results = [check_ami(settings, dbget), check_cdr(settings), check_web('--dashboard' in argv)]
    if '--test-daynight-write' in argv:
        results.append(test_daynight_write(settings))
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
