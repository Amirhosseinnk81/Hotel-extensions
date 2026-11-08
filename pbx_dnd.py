"""Do-not-disturb and call-forward state of the extensions, read from Issabel's AstDB (read-only).

IssabelPBX (FreePBX 2.11) keeps them as AstDB keys:
  DND/<ext>  = YES                   do not disturb is on
  CF/<ext>   = <number>              forward every call
  CFB/<ext>  = <number>              forward when busy
  CFU/<ext>  = <number>              forward when unavailable / not answering
A missing key means "off". The whole picture is read in one AMI session per snapshot (see
pbx_snapshot.py), never per page view, and nothing is ever written back.

Pure logic: the AMI reader is handed in, so tests use a fake.
"""
FAMILIES = (('DND', 'dnd'), ('CF', 'cf'), ('CFB', 'cfb'), ('CFU', 'cfu'))
FORWARD_KINDS = ('cf', 'cfb', 'cfu')
FORWARD_LABELS = {'cf': 'همیشه', 'cfb': 'هنگام اشغال', 'cfu': 'هنگام عدم پاسخ'}
SNAPSHOT_NAME = 'dnd'
MAX_DESTINATION = 40


def read_snapshot(reader, extensions):
    """{'flags': {ext: {'dnd': True, 'cf': '0912...', ...}}, 'scanned': [ext, ...]} for these extensions.

    Only extensions with something switched on appear in flags; 'scanned' tells which extensions were
    looked at, so an extension that is not in it is "unknown", not "off"."""
    extensions = sorted({str(e) for e in extensions})
    flags = {}
    for family, key in FAMILIES:
        for ext, value in reader.db_get_many(family, extensions).items():
            value = ' '.join(str(value).split())[:MAX_DESTINATION]
            if not value:
                continue
            flags.setdefault(ext, {})[key] = True if key == 'dnd' else value
    return {'flags': flags, 'scanned': extensions}


def state_of(snapshot, ext):
    """What one extension shows: {'known': bool, 'dnd': bool|None, 'forwards': [{'kind', 'label', 'to'}]}.
    'known' is False when the snapshot is missing / too old or the extension was not part of it."""
    data = snapshot.data if snapshot is not None else None
    if snapshot is None or snapshot.state == 'unknown' or not data or str(ext) not in set(data.get('scanned', ())):
        return {'known': False, 'dnd': None, 'forwards': []}
    entry = data['flags'].get(str(ext), {})
    forwards = [{'kind': kind, 'label': FORWARD_LABELS[kind], 'to': entry[kind]} for kind in FORWARD_KINDS if entry.get(kind)]
    return {'known': True, 'dnd': bool(entry.get('dnd')), 'forwards': forwards}


def flagged(snapshot):
    """(dnd_extensions, [(ext, forward), ...]) from a snapshot, sorted by extension number."""
    data = snapshot.data if snapshot is not None and snapshot.state != 'unknown' else None
    if not data:
        return [], []
    order = sorted(data['flags'], key=lambda e: (len(e), e))
    dnd = [ext for ext in order if data['flags'][ext].get('dnd')]
    forwards = [(ext, {'kind': kind, 'label': FORWARD_LABELS[kind], 'to': data['flags'][ext][kind]})
                for ext in order for kind in FORWARD_KINDS if data['flags'][ext].get(kind)]
    return dnd, forwards
