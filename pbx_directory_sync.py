"""Compare Issabel's extension list with the hotel directory (report only; nothing is changed on the PBX).

Where the PBX list comes from, best first:
  mysql - asterisk.users (extension, display name) joined with asterisk.devices (tech), through the
          read-only MySQL user; needs two column grants, see DEPLOYMENT.md section 11-8.
  ami   - the SIP peer / PJSIP endpoint names (no display names, so no name comparison).
Only numeric extensions are compared (trunks and other peers are ignored).

The result says, per extension number:
  missing_in_book  on Issabel, not in the directory               -> can be added (after confirmation)
  in_trash         on Issabel, but the directory entry is in the recycle bin  -> restore it from the bin
  missing_in_pbx   in the directory (not in the bin), not on Issabel
  name_mismatch    Issabel's display name is neither the title nor the person of the directory entry
  kind_mismatch    the number pattern suggests room / staff but the directory says the other
  pending          created by the phone sync and still waiting for a kind
Pure logic: no Flask, no database.
"""
import re

SNAPSHOT_NAME = 'pbx_directory'
NUMERIC = re.compile(r'^[0-9]{1,6}$')
MYSQL_SQL = ('SELECT u.extension AS extension, u.name AS name, d.tech AS tech '
             'FROM asterisk.users u LEFT JOIN asterisk.devices d ON d.id = u.extension')
MAX_EXTENSIONS = 10000
_ARABIC = str.maketrans({'ي': 'ی', 'ك': 'ک', 'ى': 'ی', 'ۀ': 'ه', 'ة': 'ه', '‌': ' ', '‏': '', '‎': ''})


def read_pbx_extensions(cdr_client=None, ami_reader=None):
    """{'source': 'mysql' | 'ami', 'extensions': {ext: {'name': str | None, 'tech': str | None}}}"""
    extensions = {}
    if cdr_client is not None:
        for row in cdr_client.select(MYSQL_SQL, max_rows=MAX_EXTENSIONS):
            ext = str(row.get('extension') or '').strip()
            if NUMERIC.match(ext):
                extensions[ext] = {'name': ' '.join(str(row.get('name') or '').split())[:150] or None,
                                   'tech': (row.get('tech') or None)}
        return {'source': 'mysql', 'extensions': extensions}
    driver = ami_reader.driver()
    for name in ami_reader.peer_names():
        if NUMERIC.match(name):
            extensions[name] = {'name': None, 'tech': driver}
    return {'source': 'ami', 'extensions': extensions}


def normalize_name(text):
    """Loose form for comparing names: Arabic letters as Persian, no case, no extra spaces or punctuation."""
    text = str(text or '').translate(_ARABIC).casefold()
    text = re.sub(r'[\s\-_.،,()]+', ' ', text)
    return text.strip()


def _sort(ext):
    return (len(ext), ext)


def compare(pbx, book, suggest_kind):
    """pbx: {ext: {'name', 'tech'}}; book: [{'extension', 'title', 'person_name', 'kind', 'is_deleted', 'is_active'}].
    suggest_kind(ext) -> 'room' / 'staff' (number pattern). Returns lists of dicts, each sorted by number."""
    entries = {str(e['extension']): e for e in book}
    result = {key: [] for key in ('missing_in_book', 'in_trash', 'missing_in_pbx', 'name_mismatch', 'kind_mismatch', 'pending')}
    for ext in sorted(pbx, key=_sort):
        info, entry = pbx[ext], entries.get(ext)
        if entry is None:
            result['missing_in_book'].append({'extension': ext, 'name': info.get('name'), 'tech': info.get('tech'),
                                              'suggested_kind': suggest_kind(ext)})
            continue
        if entry.get('is_deleted'):
            result['in_trash'].append({'extension': ext, 'name': info.get('name'), 'title': entry.get('title')})
            continue
        name = info.get('name')
        if name and normalize_name(name) not in {normalize_name(entry.get('title')), normalize_name(entry.get('person_name'))}:
            result['name_mismatch'].append({'extension': ext, 'name': name, 'title': entry.get('title'),
                                            'person_name': entry.get('person_name')})
        kind = entry.get('kind')
        if kind == 'pending':
            result['pending'].append({'extension': ext, 'title': entry.get('title'), 'suggested_kind': suggest_kind(ext)})
        elif kind in ('room', 'staff') and suggest_kind(ext) != kind:
            result['kind_mismatch'].append({'extension': ext, 'title': entry.get('title'), 'kind': kind,
                                            'suggested_kind': suggest_kind(ext)})
    for ext in sorted(entries, key=_sort):
        entry = entries[ext]
        if ext not in pbx and not entry.get('is_deleted') and NUMERIC.match(ext):
            result['missing_in_pbx'].append({'extension': ext, 'title': entry.get('title'), 'kind': entry.get('kind'),
                                             'is_active': bool(entry.get('is_active', True))})
    return result


def counts(result):
    return {key: len(items) for key, items in result.items()}


def default_title(ext, name, kind):
    """Title for an extension added from Issabel: its display name, else 'اتاق 201' / 'داخلی 120'."""
    if name:
        return name[:150]
    return ('اتاق ' if kind == 'room' else 'داخلی ') + ext
