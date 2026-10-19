"""Read a .env file (one KEY=VALUE per line) into os.environ, before the app reads its settings.

    PBX_WEB_HOST=https://192.168.1.100
    PBX_WEB_PASS="pass with #hash"     # quote a value that contains " #" or leading / trailing spaces
    export PBX_SYNC_INTERVAL=30        # a leading "export " is accepted

- A variable that is already set in the system wins over the file, so a service manager or a
  PowerShell session can still override one value without editing .env.
- Notepad's "UTF-8 with BOM" and the UTF-16 that PowerShell's `>` writes are both understood.
- PBX_DOTENV=0 switches the loader off (the tests do this so a developer's real .env cannot leak in).
- Values are never printed or logged here; load_dotenv() returns only the names it set.
"""
import os
import re

_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_QUOTED = re.compile(r'^(["\'])(.*?)\1\s*(?:#.*)?$')
_INLINE_COMMENT = re.compile(r'\s+#')
_OFF = ('0', 'false', 'no', 'off')


def decode(raw):
    """File bytes -> text (UTF-8 with or without BOM, or UTF-16 with BOM)."""
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16', errors='replace')
    return raw.decode('utf-8-sig', errors='replace')


def parse_env_text(text):
    """Text -> ([(name, value), ...], [(line_number, problem), ...]). Blank and # lines are skipped."""
    pairs, problems = [], []
    for number, line in enumerate(text.replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if re.match(r'export\s', stripped):
            stripped = stripped[6:].lstrip()
        if '=' not in stripped:
            problems.append((number, 'no "=" in this line'))
            continue
        name, _, raw = stripped.partition('=')
        name, raw = name.strip(), raw.strip()
        if not _NAME.match(name):
            problems.append((number, 'the name before "=" is not a valid variable name'))
            continue
        quoted = _QUOTED.match(raw)
        if quoted:
            value = quoted.group(2)
        elif raw[:1] in ('"', "'"):
            problems.append((number, 'a quote is opened but never closed'))
            continue
        else:
            value = _INLINE_COMMENT.split(raw, 1)[0].strip()
        pairs.append((name, value))
    return pairs, problems


def env_path(base_dir):
    return os.path.join(base_dir, '.env')


def load_dotenv(path, environ=None):
    """Put the file's variables into the environment (never replacing one that is already set).
    Returns the names that were set. A missing or unreadable file is not an error."""
    environ = os.environ if environ is None else environ
    if environ.get('PBX_DOTENV', '1').strip().lower() in _OFF:
        return []
    try:
        with open(path, 'rb') as handle:
            text = decode(handle.read())
    except OSError:
        return []
    loaded = []
    for name, value in parse_env_text(text)[0]:
        if name not in environ:
            environ[name] = value
            loaded.append(name)
    return loaded
