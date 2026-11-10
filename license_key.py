"""Time-limited license keys for Araz Desk (see the LICENSE file, section 3).

A license key is a small signed text: who it is for and until when. The developer signs it with a
private key that never leaves their own computer (tools/make_license.py); the app only holds the
public key (license_public.json next to app.py) and can check a key but never make one, so changing
the date inside a key makes it invalid.

    LICENSE_KEY=<payload>.<signature>        in .env on the server

What the app does with it (status() below):
  unmanaged  no license_public.json: licensing is not set up, nothing is checked (development)
  valid      the key is good; 'days_left' until it ends ('expiring' is True in the last 30 days)
  grace      ended, but inside the grace days written in the key: everything works, with a warning
  readonly   ended (or the key is missing / not valid): the public directory, the phone sync and the
             alerts keep working; the admin panel can be looked at but nothing can be changed

Signature: RSA with PKCS#1 v1.5 padding and SHA-256, written with the standard library only so the
server needs no extra package. Verification rebuilds the expected padded block and compares it whole.
Honest limit: the code on the server is readable Python; someone with access to the server who can
program can remove the check. The key keeps honest use honest; the LICENSE text is what binds.
"""
import base64
import hashlib
import json
import os
import secrets
from datetime import date, timedelta

PUBLIC_KEY_FILE = 'license_public.json'
PUBLIC_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), PUBLIC_KEY_FILE)
WARN_DAYS = 30
DEFAULT_DAYS = 365
DEFAULT_GRACE_DAYS = 14
MAX_KEY_LENGTH = 4000
_SHA256_PREFIX = bytes.fromhex('3031300d060960864801650304020105000420')  # DigestInfo header for SHA-256


class LicenseError(Exception):
    """The key cannot be accepted. The message is English, for the log; pages show their own text."""


# ---- encoding ------------------------------------------------------------------------------------
def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _unb64(text):
    try:
        return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise LicenseError('the key is not valid base64') from exc


def _padded_digest(message, size):
    """EMSA-PKCS1-v1_5 block of `size` bytes for the SHA-256 of message."""
    tail = _SHA256_PREFIX + hashlib.sha256(message).digest()
    if size < len(tail) + 11:
        raise LicenseError('the signing key is too short')
    return b'\x00\x01' + b'\xff' * (size - len(tail) - 3) + b'\x00' + tail


# ---- keys ----------------------------------------------------------------------------------------
_SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]


def _is_prime(n, rounds=40):
    if n < 2 or n % 2 == 0:
        return n == 2
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for _ in range(rounds):  # Miller-Rabin
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits):
    while True:
        candidate = secrets.randbits(bits) | (3 << (bits - 2)) | 1  # top two bits set: the product has full length
        if _is_prime(candidate):
            return candidate


def generate_keypair(bits=3072):
    """(private, public) dicts with hex numbers. Takes a few seconds for 3072 bits."""
    e = 65537
    while True:
        p, q = _random_prime(bits // 2), _random_prime(bits - bits // 2)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    n = p * q
    return {'n': format(n, 'x'), 'e': e, 'd': format(pow(e, -1, phi), 'x')}, {'n': format(n, 'x'), 'e': e}


def load_public_key(path=None):
    """The public key dict, or None when the file does not exist (licensing not set up)."""
    path = PUBLIC_KEY_PATH if path is None else path
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8-sig') as handle:
            data = json.load(handle)
        return {'n': format(int(str(data['n']), 16), 'x'), 'e': int(data['e'])}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LicenseError(f'{PUBLIC_KEY_FILE} cannot be read') from exc


# ---- sign / verify -------------------------------------------------------------------------------
def make_payload(customer, expires, issued=None, grace_days=DEFAULT_GRACE_DAYS, license_id=None):
    issued = issued or date.today()
    customer = ' '.join(str(customer or '').split())[:120]
    if not customer:
        raise LicenseError('a license needs a customer name')
    if expires < issued:
        raise LicenseError('the license would end before it starts')
    return {'product': 'Araz Desk', 'customer': customer, 'issued': issued.isoformat(), 'expires': expires.isoformat(),
            'grace_days': max(0, min(90, int(grace_days))), 'id': license_id or secrets.token_hex(6)}


def sign(private, payload):
    """Payload dict -> license key text. Only tools/make_license.py calls this (it needs the private key)."""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    n, d = int(private['n'], 16), int(private['d'], 16)
    size = (n.bit_length() + 7) // 8
    signature = pow(int.from_bytes(_padded_digest(body, size), 'big'), d, n)
    return _b64(body) + '.' + _b64(signature.to_bytes(size, 'big'))


def verify(public, key):
    """License key text -> payload dict. Raises LicenseError for anything that is not a key we signed."""
    key = ''.join(str(key or '').split())
    if not key:
        raise LicenseError('no license key')
    if len(key) > MAX_KEY_LENGTH or key.count('.') != 1:
        raise LicenseError('the key does not look like a license key')
    body_text, signature_text = key.split('.')
    body, signature = _unb64(body_text), _unb64(signature_text)
    n, e = int(public['n'], 16), int(public['e'])
    size = (n.bit_length() + 7) // 8
    if len(signature) != size or int.from_bytes(signature, 'big') >= n:
        raise LicenseError('the signature does not fit this product key')
    block = pow(int.from_bytes(signature, 'big'), e, n).to_bytes(size, 'big')
    if not secrets.compare_digest(block, _padded_digest(body, size)):
        raise LicenseError('the signature is not valid (the key was changed or is for another product)')
    try:
        payload = json.loads(body.decode('utf-8'))
        date.fromisoformat(payload['expires'])
        date.fromisoformat(payload['issued'])
        payload['grace_days'] = max(0, int(payload.get('grace_days', 0)))
        if payload.get('product') != 'Araz Desk' or not payload.get('customer'):
            raise ValueError('wrong product')
    except (ValueError, KeyError, TypeError) as exc:
        raise LicenseError('the key is signed but its content is not a license') from exc
    return payload


# ---- status --------------------------------------------------------------------------------------
def evaluate(payload, today):
    """A verified payload and today's date -> what the app shows and enforces."""
    expires, issued = date.fromisoformat(payload['expires']), date.fromisoformat(payload['issued'])
    days_left = (expires - today).days  # the last day itself still counts: 0 = ends tonight
    grace_until = expires + timedelta(days=payload['grace_days'])
    if days_left >= 0:
        state = 'valid'
    elif today <= grace_until:
        state = 'grace'
    else:
        state = 'readonly'
    return {'state': state, 'reason': 'expired' if state == 'readonly' else None, 'customer': payload['customer'],
            'license_id': payload.get('id', ''), 'issued': issued, 'expires': expires, 'grace_until': grace_until,
            'days_left': days_left, 'grace_left': (grace_until - today).days if state == 'grace' else None,
            'expiring': state == 'valid' and days_left <= WARN_DAYS}


_EMPTY = {'customer': None, 'license_id': '', 'issued': None, 'expires': None, 'grace_until': None, 'days_left': None,
          'grace_left': None, 'expiring': False}
_verified = {}  # (public n, key) -> payload or LicenseError; a signature is checked once, the date every time


def status(key, today, public_key_path=None):
    """The whole decision: see the module text. Never raises."""
    try:
        public = load_public_key(public_key_path)
    except LicenseError:
        return {**_EMPTY, 'state': 'readonly', 'reason': 'bad_public_key'}
    if public is None:
        return {**_EMPTY, 'state': 'unmanaged', 'reason': None}
    key = ''.join(str(key or '').split())
    if not key:
        return {**_EMPTY, 'state': 'readonly', 'reason': 'missing'}
    cache_key = (public['n'], key)
    if cache_key not in _verified:
        if len(_verified) > 20:
            _verified.clear()
        try:
            _verified[cache_key] = verify(public, key)
        except LicenseError as exc:
            _verified[cache_key] = exc
    payload = _verified[cache_key]
    if isinstance(payload, LicenseError):
        return {**_EMPTY, 'state': 'readonly', 'reason': 'invalid'}
    return evaluate(payload, today)
