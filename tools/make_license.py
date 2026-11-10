"""Developer tool: create the signing key once, then issue license keys for Araz Desk.

Run it on the DEVELOPER's computer only. The private key must never be copied to a customer's server.

    python tools/make_license.py init
        Creates the key pair once: the private key in your user folder (~/.araz-desk/license_private.json)
        and the public key as license_public.json next to app.py. Commit license_public.json; from the
        version that contains it, the app asks for a valid LICENSE_KEY.

    python tools/make_license.py issue --customer "Hotel Araz"
        Prints a key for 365 days from today (options: --days 365, --from 2026-11-10, --grace 14).
        Put the printed line in .env on the server and restart the service.

    python tools/make_license.py show <key>
        Checks a key against license_public.json and prints what is inside.

Options for all commands: --private <file> (where the private key is), --public <file>.
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, ROOT)

import license_key  # noqa: E402

DEFAULT_PRIVATE = os.path.join(os.path.expanduser('~'), '.araz-desk', 'license_private.json')
DEFAULT_PUBLIC = os.path.join(ROOT, license_key.PUBLIC_KEY_FILE)


def inside(path, folder):
    return os.path.abspath(path).lower().startswith(os.path.abspath(folder).lower() + os.sep)


def cmd_init(args):
    if inside(args.private, ROOT):
        sys.exit('Refusing to keep the private key inside the project folder: it would be copied to the server with the app.')
    for path in (args.private, args.public):
        if os.path.exists(path):
            sys.exit(f'{path} already exists. A new key pair would make every license issued so far invalid; '
                     'move the old files away yourself if that is really what you want.')
    print(f'Creating a {args.bits}-bit key pair (a few seconds)...')
    private, public = license_key.generate_keypair(args.bits)
    os.makedirs(os.path.dirname(os.path.abspath(args.private)), exist_ok=True)
    with open(args.private, 'w', encoding='utf-8') as handle:
        json.dump(private, handle)
    with open(args.public, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(public, handle, indent=1)
        handle.write('\n')
    print(f'Private key: {args.private}')
    print('  Keep it secret and make a backup (e.g. an encrypted USB stick). Without it you cannot renew licenses;')
    print('  with it anyone can issue them.')
    print(f'Public key:  {args.public}  (commit this file)')
    return 0


def load_private(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except OSError:
        sys.exit(f'Private key not found at {path}. Run "init" first, or pass --private <file>.')


def describe(payload, today):
    info = license_key.evaluate(payload, today)
    print(f"  customer: {payload['customer']}")
    print(f"  id:       {payload.get('id', '')}")
    print(f"  valid:    {payload['issued']} to {payload['expires']}  (+{payload['grace_days']} grace days, admin read-only after {info['grace_until']})")
    print(f"  today:    {info['state']}" + (f", {info['days_left']} day(s) left" if info['state'] == 'valid' else ''))


def cmd_issue(args):
    private = load_private(args.private)
    try:
        start = date.fromisoformat(args.start) if args.start else date.today()
    except ValueError:
        sys.exit('--from must look like 2026-11-10')
    try:
        payload = license_key.make_payload(args.customer, start + timedelta(days=args.days), start, args.grace)
        key = license_key.sign(private, payload)
        public = license_key.load_public_key(args.public)
        if public is None:
            sys.exit(f'{args.public} not found: the app could not check this key.')
        license_key.verify(public, key)  # the key must work with the public key the app ships
    except license_key.LicenseError as exc:
        sys.exit(f'Could not issue the license: {exc}')
    describe(payload, date.today())
    print('\nPut this line in .env on the server, then restart the service:\n')
    print(f'LICENSE_KEY={key}')
    return 0


def cmd_show(args):
    try:
        public = license_key.load_public_key(args.public)
        if public is None:
            sys.exit(f'{args.public} not found.')
        payload = license_key.verify(public, args.key)
    except license_key.LicenseError as exc:
        sys.exit(f'NOT VALID: {exc}')
    print('Valid signature.')
    describe(payload, date.today())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Araz Desk license keys (developer tool)')
    parser.add_argument('--private', default=DEFAULT_PRIVATE)
    parser.add_argument('--public', default=DEFAULT_PUBLIC)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--bits', type=int, default=3072, choices=(2048, 3072, 4096))
    issue = commands.add_parser('issue')
    issue.add_argument('--customer', required=True)
    issue.add_argument('--days', type=int, default=license_key.DEFAULT_DAYS)
    issue.add_argument('--from', dest='start', default='')
    issue.add_argument('--grace', type=int, default=license_key.DEFAULT_GRACE_DAYS)
    show = commands.add_parser('show')
    show.add_argument('key')
    args = parser.parse_args(argv)
    return {'init': cmd_init, 'issue': cmd_issue, 'show': cmd_show}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
