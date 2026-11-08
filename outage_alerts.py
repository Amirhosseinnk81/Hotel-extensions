"""Turns confirmed outage events into the alerts the browser shows (pure logic, no Flask / database).

An event is a dict: id, kind ('offline' = an outage was confirmed, 'online' = the phone came back),
batch (anything that is equal for every event of one sync cycle), extension, title, detail.
  detail: for 'offline' the local time the outage began ('14:02'); for 'online' how long it lasted ('00:13:11').

Storm control: when more than `threshold` phones go offline in the same sync cycle (a switch died, the
PBX rebooted) one summary alert replaces the individual ones, so the screen is not flooded.
Recovery alerts are quiet (no sound) and can be switched off entirely.
"""
from collections import OrderedDict

OFFLINE = 'offline'
ONLINE = 'online'
LISTED_IN_SUMMARY = 8  # extensions named in the body of a summary alert

_PERSIAN_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def persian_digits(value):
    return str(value).translate(_PERSIAN_DIGITS)


def phone_label(extension, title):
    """'داخلی 255 (پذیرش 3)' - the title is optional."""
    title = ' '.join(str(title or '').split())
    return f'داخلی {extension} ({title})' if title else f'داخلی {extension}'


def _single(event, url):
    label = phone_label(event['extension'], event.get('title'))
    detail = event.get('detail') or ''
    if event['kind'] == OFFLINE:
        return {'id': event['id'], 'kind': OFFLINE, 'tag': f"outage-offline-{event['id']}", 'quiet': False,
                'title': f'☎ {label} آفلاین شد', 'body': f'قطع از ساعت {detail}' if detail else '', 'url': url}
    return {'id': event['id'], 'kind': ONLINE, 'tag': f"outage-online-{event['id']}", 'quiet': True,
            'title': f'✅ {label} دوباره وصل شد', 'body': f'مدت قطعی: {detail}' if detail else '', 'url': url}


def _summary(kind, items, url):
    count = persian_digits(len(items))
    listed = [str(item['extension']) for item in items[:LISTED_IN_SUMMARY]]
    body = 'داخلی‌ها: ' + '، '.join(listed)
    if len(items) > LISTED_IN_SUMMARY:
        body += f'، و {persian_digits(len(items) - LISTED_IN_SUMMARY)} مورد دیگر'
    if kind == OFFLINE:
        title, quiet = f'☎ {count} تلفن همزمان آفلاین شدند', False
    else:
        title, quiet = f'✅ {count} تلفن دوباره وصل شدند', True
    return {'id': items[-1]['id'], 'kind': f'summary-{kind}', 'tag': f"outage-summary-{kind}-{items[0]['id']}",
            'quiet': quiet, 'title': title, 'body': body, 'url': url}


RAISED = 'raised'
_HEALTH_TEXT = {  # (subject kind, raised?) -> (title, body); {s} = subject, {d} = detail
    ('disk', True): ('💽 دیسک ایزابل در حال پر شدن است', '{s}: {d}'),
    ('disk', False): ('✅ فضای دیسک ایزابل عادی شد', '{s}: {d}'),
    ('cpu', True): ('⚙ بار پردازنده‌ی ایزابل بالاست', 'CPU: {d}'),
    ('cpu', False): ('✅ بار پردازنده‌ی ایزابل عادی شد', 'CPU: {d}'),
    ('asterisk', True): ('⛔ سرویس Asterisk روی ایزابل متوقف است', ''),
    ('asterisk', False): ('✅ سرویس Asterisk دوباره اجرا شد', ''),
    ('trunk', True): ('📞 ترانک {s} رجیستر نیست', '{d}'),
    ('trunk', False): ('✅ ترانک {s} دوباره رجیستر شد', ''),
}


def plan_health_alerts(events, url=''):
    """Issabel health events (kind raised / cleared, extension = disk / cpu / asterisk / trunk) -> alerts.
    Problems ring; recoveries are quiet. There are few of these, so no summary is needed."""
    alerts = []
    for event in events:
        raised = event['kind'] == RAISED
        title, body = _HEALTH_TEXT.get((event['extension'], raised), ('⚠ ' + str(event.get('title') or ''), ''))
        values = {'s': event.get('title') or '', 'd': event.get('detail') or ''}
        alerts.append({'id': event['id'], 'kind': f"pbx-{event['kind']}", 'tag': f"pbx-{event['kind']}-{event['id']}", 'quiet': not raised,
                       'title': title.format(**values), 'body': body.format(**values).strip(': ').strip(), 'url': url})
    return alerts


def plan_alerts(events, threshold=5, on_recovery=True, url=''):
    """Events (oldest first) -> alerts to show, ordered by id. A group of the same kind from the same
    sync cycle with more than `threshold` members becomes one summary alert."""
    groups = OrderedDict()
    for event in events:
        if event['kind'] == ONLINE and not on_recovery:
            continue
        groups.setdefault((event['kind'], event['batch']), []).append(event)
    alerts = []
    for (kind, _batch), items in groups.items():
        if len(items) > threshold:
            alerts.append(_summary(kind, items, url))
        else:
            alerts.extend(_single(item, url) for item in items)
    alerts.sort(key=lambda alert: alert['id'])
    return alerts
