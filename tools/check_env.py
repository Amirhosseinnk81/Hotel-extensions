"""Show what the .env file next to app.py will do, without starting the app.

    python tools/check_env.py

Lists each variable, whether the value comes from .env or is already set in the system (which wins),
and lines that could not be read. Passwords and keys are never printed. Names that are not in
.env.example are reported as possible typos.
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

import envfile  # noqa: E402

SECRET_WORDS = ('PASS', 'SECRET', 'KEY', 'TOKEN')
# A connection only works when all of its variables are set; half a group is almost always a typo.
GROUPS = (
    ('AMI', ('PBX_AMI_HOST', 'PBX_AMI_USER', 'PBX_AMI_SECRET')),
    ('web panel', ('PBX_WEB_HOST', 'PBX_WEB_USER', 'PBX_WEB_PASS')),
    ('CDR database', ('PBX_CDR_HOST', 'PBX_CDR_USER', 'PBX_CDR_PASS')),
    ('AMI write user (day / night)', ('PBX_AMI_WRITE_USER', 'PBX_AMI_WRITE_SECRET')),
)
CHOICES = {'PBX_DAYNIGHT_WRITE_METHOD': ('setvar', 'dbput'), 'PBX_AMI_AUTH': ('md5', 'plain'), 'PBX_SIP_DRIVER': ('auto', 'sip', 'pjsip'), 'PBX_SOURCE': ('auto', 'ami', 'web')}


def known_names():
    try:
        with open(os.path.join(ROOT, '.env.example'), encoding='utf-8-sig') as handle:
            return set(re.findall(r'^\s*#?\s*([A-Z][A-Z0-9_]*)=', handle.read(), re.M))
    except OSError:
        return set()


def group_problems(values):
    """values: {name: value} (the .env file over the system) -> ['CDR database: PBX_CDR_PASS is missing', ...]"""
    problems = []
    for label, names in GROUPS:
        present = [n for n in names if (values.get(n) or '').strip()]
        if present and len(present) < len(names):
            missing = ', '.join(n for n in names if n not in present)
            problems.append(f'{label}: {missing} missing, so this connection stays off')
    for name, allowed in CHOICES.items():
        value = (values.get(name) or '').strip().lower()
        if value and value not in allowed:
            problems.append(f'{name}={value!r} is not one of {", ".join(allowed)}; the default is used')
    return problems


def main():
    path = envfile.env_path(os.path.abspath(ROOT))
    print(f'File: {path}')
    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except OSError:
        sys.exit('Not found. Copy .env.example to .env (same folder as app.py) and fill in the values.')
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        print('Note: the file is UTF-16 (PowerShell ">" does that); it is read fine, UTF-8 is just more common.')
    pairs, problems = envfile.parse_env_text(envfile.decode(raw))
    if os.environ.get('PBX_DOTENV', '1').strip().lower() in envfile._OFF:
        print('PBX_DOTENV=0 is set in the system: the app will NOT read this file.')
    known = known_names()
    print(f'{len(pairs)} variable(s):')
    for name, value in pairs:
        shown = '<hidden>' if any(word in name for word in SECRET_WORDS) else (value or '(empty)')
        if name in os.environ:
            origin = 'already set in the system -> the system value is used, the one in .env is ignored'
        else:
            origin = 'from .env'
        typo = '   <-- not in .env.example: typo?' if known and name not in known else ''
        print(f'  {name:<28} {shown:<30} {origin}{typo}')
    for number, problem in problems:
        print(f'  line {number}: {problem} (ignored)')
    if not pairs and not problems:
        print('  (the file has no settings; every line is blank or a comment)')
    effective = dict(pairs)
    effective.update({name: value for name, value in os.environ.items() if name.startswith('PBX_')})  # the system wins
    for problem in group_problems(effective):
        print(f'  check: {problem}')


if __name__ == '__main__':
    main()
