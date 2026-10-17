import csv
import glob
import hashlib
import io
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from logging.handlers import RotatingFileHandler



from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from openpyxl import Workbook, load_workbook
from arabic_reshaper import reshape
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle
from sqlalchemy import func, or_
from xml.sax.saxutils import escape as xml_escape
from sqlalchemy.orm.attributes import flag_modified

import outage_alerts
import outage_monitor
import outage_report
from pbx_sync import AMIClient, AMIError, fetch_peers, fetch_user_agent, parse_user_agent, shared_scraper
from werkzeug.datastructures import FileStorage
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
IMPORT_TMP_DIR = os.path.join(BASE_DIR, 'tmp_imports')
SECRET_KEY_FILE = os.path.join(BASE_DIR, '.secret_key')
ADMIN_PREFIX_FILE = os.path.join(BASE_DIR, '.admin_prefix')

# SHA-256 of secret keys that were committed to the public Git history and
# must never be used again. If the key loaded at startup matches one of
# these, a fresh random key is generated and saved instead.
COMPROMISED_SECRET_KEY_HASHES = {
    'b3199ef09757effb2b0b4db2a9283b8af6e22143a73ea160fab9e979df2b2a0c',
}
# Passwords that ship with the project. Any account still using one of them
# is forced to change it at next login.
DEFAULT_PASSWORDS = ('admin123',)

# Extension kinds. Only "staff" extensions are visible to the public (staff
# directory); "room" (guest rooms) and "pending" (found on the PBX but not
# reviewed yet) are visible to logged-in admins only.
EXTENSION_KINDS = ('staff', 'room', 'pending')
KIND_LABELS = {'staff': 'پرسنل / اداری', 'room': 'اتاق مهمان', 'pending': 'در انتظار بررسی'}
KIND_ALIASES = {
    'staff': 'staff', 'پرسنل': 'staff', 'اداری': 'staff', 'پرسنل / اداری': 'staff', 'پرسنل/اداری': 'staff',
    'room': 'room', 'اتاق': 'room', 'اتاق مهمان': 'room', 'مهمان': 'room',
    'pending': 'pending', 'در انتظار بررسی': 'pending', 'انتظار': 'pending',
}
# Guest-room extensions are <floor><room>: 201..227 on floor 2, 301..327 on
# floor 3, ..., 1001..1027 on floor 10, ..., 2301..2327 on floor 23.
ROOM_FLOOR_MIN = int(os.getenv('ROOM_FLOOR_MIN', '2'))
ROOM_FLOOR_MAX = int(os.getenv('ROOM_FLOOR_MAX', '23'))
ROOM_NUMBER_MAX = int(os.getenv('ROOM_NUMBER_MAX', '27'))
# Phone status older than this many minutes is shown as "unknown".
PBX_STATUS_STALE_MINUTES = int(os.getenv('PBX_STATUS_STALE_MINUTES', '10'))
PBX_SLOW_MS = int(os.getenv('PBX_SLOW_MS', '150'))  # an online phone at or above this latency gets the yellow badge
PHONE_STATUS_LABELS = {'online': 'تلفن آنلاین', 'offline': 'تلفن آفلاین', 'unknown': 'وضعیت تلفن نامشخص'}
PHONE_SLOW_LABEL = 'تلفن آنلاین (تأخیر بالا)'

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login'


def _is_compromised_secret_key(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest() in COMPROMISED_SECRET_KEY_HASHES


def get_or_create_secret_key():
    """Use SECRET_KEY from the environment if set; otherwise generate a
    random key once and persist it to a local file so it survives
    restarts, instead of falling back to a hardcoded shared value.
    A key that is known to have leaked (see COMPROMISED_SECRET_KEY_HASHES)
    is never accepted: it is replaced by a new random key."""
    env_key = os.getenv('SECRET_KEY')
    if env_key and not _is_compromised_secret_key(env_key):
        return env_key
    if os.path.isfile(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r', encoding='utf-8') as f:
            key = f.read().strip()
            if key and not _is_compromised_secret_key(key):
                return key
    key = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_FILE, 'w', encoding='utf-8') as f:
            f.write(key)
    except OSError:
        pass
    return key


def get_or_create_admin_prefix():
    """The entire admin area (including the login page) is mounted under
    a secret, unguessable URL segment instead of the predictable
    '/admin' and '/login' — and no link to it is rendered anywhere in
    the public pages. Set ADMIN_URL_PREFIX in the environment to choose
    your own memorable value; otherwise a random one is generated once
    and persisted locally so it doesn't change across restarts."""
    env_prefix = os.getenv('ADMIN_URL_PREFIX')
    if env_prefix:
        return env_prefix.strip('/')
    if os.path.isfile(ADMIN_PREFIX_FILE):
        with open(ADMIN_PREFIX_FILE, 'r', encoding='utf-8') as f:
            prefix = f.read().strip()
            if prefix:
                return prefix
    prefix = 'staff-' + secrets.token_hex(4)
    try:
        with open(ADMIN_PREFIX_FILE, 'w', encoding='utf-8') as f:
            f.write(prefix)
    except OSError:
        pass
    return prefix


# Persian-capable fonts tried (in order) when PDF_FONT_PATH is not set. Any
# .ttf dropped into a "fonts" folder next to app.py is tried first.
PDF_FONT_CANDIDATES = (
    r'C:\Windows\Fonts\Tahoma.ttf',
    '/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf',
    '/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
)


def resolve_pdf_font(configured=None):
    """Return (font_path, error_message); exactly one of them is None."""
    if configured:
        if os.path.isfile(configured):
            return configured, None
        return None, f'فونت تعیین‌شده در PDF_FONT_PATH پیدا نشد: {configured}'
    local_fonts = sorted(glob.glob(os.path.join(BASE_DIR, 'fonts', '*.ttf')))
    for path in local_fonts + list(PDF_FONT_CANDIDATES):
        if os.path.isfile(path):
            return path, None
    return None, ('هیچ فونت فارسی روی این سرور پیدا نشد. یک فایل TTF فارسی (مثلاً Vazirmatn) را در پوشهٔ fonts کنار app.py '
                  'بگذارید یا مسیر آن را در متغیر محیطی PDF_FONT_PATH ست کنید.')


class TrustedProxyFix:
    """Apply ProxyFix (X-Forwarded-* headers) only when the connection really
    comes from a trusted reverse proxy. A client that reaches the app
    directly can otherwise forge X-Forwarded-For and dodge the login
    rate limit. Trusted addresses: TRUSTED_PROXIES (comma separated),
    default 127.0.0.1 and ::1 (Nginx / IIS-ARR on the same machine)."""

    def __init__(self, wsgi_app, trusted):
        self.wsgi_app = wsgi_app
        self.trusted = set(trusted)
        self.proxy_fix = ProxyFix(wsgi_app, x_for=1, x_proto=1, x_host=1)

    def __call__(self, environ, start_response):
        if environ.get('REMOTE_ADDR') in self.trusted:
            return self.proxy_fix(environ, start_response)
        return self.wsgi_app(environ, start_response)


def cell_text(value):
    """Cell value -> clean string; Excel numbers such as 9121234567.0 lose the '.0'."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def parse_present(value):
    return cell_text(value).lower() in ('حاضر', 'present', '1', 'true', 'yes', 'بله')


def is_room_number(extension):
    """True when the number follows the guest-room pattern (see ROOM_* settings)."""
    ext = str(extension or '').strip()
    if not re.fullmatch(r'[1-9][0-9]{2,3}', ext):
        return False
    floor, room = (int(ext[0]), int(ext[1:])) if len(ext) == 3 else (int(ext[:2]), int(ext[2:]))
    return ROOM_FLOOR_MIN <= floor <= ROOM_FLOOR_MAX and 1 <= room <= ROOM_NUMBER_MAX


def suggest_kind(extension):
    return 'room' if is_room_number(extension) else 'staff'


def parse_kind(value):
    return KIND_ALIASES.get(cell_text(value).lower())


def normalize_mac(value):
    """Return the MAC as AA:BB:CC:DD:EE:FF, or None when it is empty or invalid.
    Accepts ':' '-' '.' separators or none at all (any letter case)."""
    text = cell_text(value)
    if not text or re.search(r'[^0-9a-fA-F:\-.\s]', text):
        return None
    digits = re.sub(r'[^0-9a-fA-F]', '', text).lower()
    if len(digits) != 12 or digits in ('000000000000', 'ffffffffffff'):
        return None
    return ':'.join(digits[i:i + 2] for i in range(0, 12, 2)).upper()


def display_phone_status(entry):
    """'online' / 'offline' / 'unknown', or None when there is no PBX data for the extension."""
    if entry.status_updated_at is None or not entry.reg_status:
        return None
    if datetime.utcnow() - entry.status_updated_at > timedelta(minutes=PBX_STATUS_STALE_MINUTES):
        return 'unknown'
    return entry.reg_status if entry.reg_status in ('online', 'offline') else 'unknown'


def phone_is_slow(entry):
    """True for an online phone whose last measured latency is at or above PBX_SLOW_MS."""
    return display_phone_status(entry) == 'online' and entry.rtt_ms is not None and entry.rtt_ms >= PBX_SLOW_MS


def phone_label(entry):
    """Badge text for the phone state, or '' when there is no PBX data."""
    status = display_phone_status(entry)
    if not status:
        return ''
    return PHONE_SLOW_LABEL if phone_is_slow(entry) else PHONE_STATUS_LABELS[status]


def parse_row_extras(row):
    """Optional import columns (kind / mac / phone_model) -> (values, invalid_fields).
    A blank cell never changes the stored value; an invalid one is ignored and reported."""
    values, invalid = {}, []
    if 'kind' in row:
        raw = cell_text(row.get('kind'))
        if raw:
            kind = parse_kind(raw)
            if kind:
                values['kind'] = kind
            else:
                invalid.append('kind')
    if 'mac' in row:
        raw = cell_text(row.get('mac'))
        if raw:
            mac = normalize_mac(raw)
            if mac:
                values['mac'] = mac
            else:
                invalid.append('mac')
    if 'phone_model' in row:
        raw = cell_text(row.get('phone_model'))
        if raw:
            values['phone_model'] = raw[:60]
    return values, invalid


# ---- MAC import wizard helpers ------------------------------------------------
MAC_ACTIONS_APPLIED = ('set', 'overwrite', 'create', 'same')
MAC_ACTION_LABELS = {
    'set': ('ثبت MAC جدید', 'success'),
    'overwrite': ('جایگزینی MAC', 'warning'),
    'create': ('ساخت داخلی جدید', 'info'),
    'same': ('بدون تغییر', 'secondary'),
    'skip_conflict': ('MAC متفاوتی ثبت است (رد شد)', 'danger'),
    'invalid_mac': ('MAC نامعتبر', 'danger'),
    'skip_missing': ('داخلی در دیتابیس نیست', 'secondary'),
    'skip_deleted': ('داخلی در سطل بازیافت است', 'secondary'),
    'skip_dup_ext': ('داخلی تکراری در فایل', 'secondary'),
    'skip_empty_mac': ('MAC خالی', 'secondary'),
    'skip_no_ext': ('شماره داخلی خالی', 'secondary'),
}


def _header_key(value):
    text = cell_text(value).lower().replace('_', ' ').replace('-', ' ').replace('\u200c', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def col_letter(index):
    letters, number = '', index + 1
    while number:
        number, rem = divmod(number - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def read_table(path, sheet_name=None, max_rows=5000):
    """Read a CSV/XLSX file as raw rows -> (sheet_names, rows). No header assumptions."""
    if path.lower().endswith('.csv'):
        with open(path, 'rb') as f:
            raw = f.read()
        text = ''
        for encoding in ('utf-8-sig', 'cp1256', 'latin-1'):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        return [], list(csv.reader(io.StringIO(text), dialect))[:max_rows]
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        names = workbook.sheetnames
        sheet = workbook[sheet_name] if sheet_name in names else workbook[names[0]]
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
            if len(rows) >= max_rows:
                break
        return names, rows
    finally:
        workbook.close()


def prepare_table(rows, has_header):
    """Drop empty rows (keeping original row numbers) -> (header, body [(row_no, cells)])."""
    numbered = [(i + 1, list(r)) for i, r in enumerate(rows) if any(cell_text(c) for c in r)]
    if has_header and numbered:
        return numbered[0][1], numbered[1:]
    return [], numbered


def detect_mac_columns(header, body):
    """Guess (ext_idx, mac_idx, model_idx): by header names first, then by cell contents."""
    ncols = max([len(header)] + [len(cells) for _, cells in body[:300]] + [0])
    keys = [_header_key(h) for h in header] + [''] * (ncols - len(header))
    ext_idx = mac_idx = model_idx = None
    for i, key in enumerate(keys):
        if not key:
            continue
        if mac_idx is None and ('mac' in key or 'مک' in key):
            mac_idx = i
        elif ext_idx is None and ('extension' in key or 'داخلی' in key or key in ('ext', 'exten', 'number', 'no', 'شماره')):
            ext_idx = i
        elif model_idx is None and ('model' in key or 'مدل' in key):
            model_idx = i
    sample = body[:300]

    def fraction(i, predicate):
        values = [cell_text(cells[i]) for _, cells in sample if i < len(cells) and cell_text(cells[i])]
        return sum(1 for v in values if predicate(v)) / len(values) if values else 0

    if mac_idx is None and ncols:
        best = max(range(ncols), key=lambda i: fraction(i, lambda v: normalize_mac(v) is not None))
        if fraction(best, lambda v: normalize_mac(v) is not None) >= 0.5:
            mac_idx = best
    if ext_idx is None:
        for i in range(ncols):
            if i != mac_idx and fraction(i, lambda v: re.fullmatch(r'[0-9]{1,5}', v) is not None) >= 0.6:
                ext_idx = i
                break
    return ext_idx, mac_idx, model_idx


def make_db_backup(db_path):
    """Consistent snapshot of the live SQLite file via SQLite's backup API
    (a plain file copy can catch the database mid-write)."""
    fd, tmp_path = tempfile.mkstemp(suffix='.db', dir=IMPORT_TMP_DIR)
    os.close(fd)
    try:
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(tmp_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with open(tmp_path, 'rb') as f:
            return io.BytesIO(f.read())
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# Simple in-memory login rate limiter: {ip: [timestamps of recent failures]}.
# Not shared across multiple worker processes — fine for a single-process
# deployment; a multi-worker setup would need a shared store instead.
_login_failures = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300


def is_login_rate_limited(ip):
    now = datetime.utcnow().timestamp()
    attempts = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_failures[ip] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(ip):
    _login_failures.setdefault(ip, []).append(datetime.utcnow().timestamp())


def clear_login_failures(ip):
    _login_failures.pop(ip, None)


LOCAL_TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'Asia/Tehran'))


def format_local_datetime(value, fmt='%Y/%m/%d %H:%M'):
    """All timestamps are stored in UTC (datetime.utcnow()); this converts
    to the hotel's local timezone for display. Set APP_TIMEZONE in the
    environment if the server isn't in Iran."""
    if value is None:
        return '—'
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ).strftime(fmt)


def department_icon(name):
    """Best-effort emoji icon for a department, matched by keyword
    (word-boundary aware, so e.g. the 'IT' keyword won't match inside
    'Security')."""
    if not name:
        return '🏢'
    lowered = name.lower()
    mapping = [
        (['فنی', 'it', 'تکنولوژی', 'information technology'], '💻'),
        (['engineer', 'engeering', 'مهندسی'], '🛠'),
        (['رستوران', 'غذا', 'آشپز', 'f&b', 'food', 'beverage'], '🍽'),
        (['پذیرش', 'فرانت', 'front', 'reception'], '🛎'),
        (['خانه‌داری', 'خانه داری', 'housekeeping'], '🧹'),
        (['حراست', 'امنیت', 'security'], '🔒'),
        (['مالی', 'حسابداری', 'finance', 'accounting'], '💰'),
        (['بازاریابی', 'فروش', 'marketing'], '📣'),
        (['منابع انسانی', 'hr', 'human resources'], '👥'),
        (['بهداری', 'درمان', 'health', 'clinic'], '🩺'),
        (['hse', 'safety', 'ایمنی'], '🦺'),
        (['ورزش', 'باشگاه', 'sport', 'entertainment'], '🎾'),
        (['گل', 'flower'], '💐'),
        (['لاندری', 'خشکشویی', 'laundry'], '🧺'),
        (['آرایشگاه', 'سالن زیبایی', 'salon'], '💇'),
        (['انبار', 'warehouse', 'store', 'stock'], '📦'),
        (['خرید', 'بازرگانی', 'commerce', 'shopping', 'purchasing'], '🛍'),
        (['مدیریت', 'management'], '🗂'),
    ]
    for keywords, icon in mapping:
        for kw in keywords:
            if re.search(r'(?<![\w])' + re.escape(kw) + r'(?![\w])', lowered):
                return icon
    return '🏢'


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    is_admin = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    working_hours = db.Column(db.String(120))
    extension_entries = db.relationship('Extension', backref='department', lazy=True)


class Extension(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    extension = db.Column(db.String(20), unique=True, nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False, index=True)
    person_name = db.Column(db.String(150), index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True, index=True)
    location = db.Column(db.String(150))
    email = db.Column(db.String(150))
    mobile = db.Column(db.String(30))
    is_present = db.Column(db.Boolean, default=False, nullable=False)
    notes = db.Column(db.Text)
    # staff = public directory, room = guest room, pending = unreviewed (both hidden from the public)
    kind = db.Column(db.String(10), default='staff', nullable=False, index=True)
    mac = db.Column(db.String(17), index=True)  # normalized AA:BB:CC:DD:EE:FF
    phone_model = db.Column(db.String(60))
    # Live data written by the PBX sync (admin-only; never shown publicly except the online/offline state)
    reg_status = db.Column(db.String(10))  # online / offline
    last_ip = db.Column(db.String(45))
    rtt_ms = db.Column(db.Integer)
    last_seen_at = db.Column(db.DateTime)
    user_agent = db.Column(db.String(200))
    status_updated_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, index=True)
    deleted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ActivityLog(db.Model):
    """Records admin-panel changes only (extensions, departments, imports)."""
    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(120), nullable=False)
    action = db.Column(db.String(30), nullable=False)  # created / updated / deleted / imported
    entity_type = db.Column(db.String(30), nullable=False)  # extension / department
    entity_label = db.Column(db.String(200), nullable=False)
    details = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PhoneOutage(db.Model):
    """One row per period a monitored phone was not registered on the PBX (see outage_monitor.py).

    started_at is when the sync first saw it offline; ended_at stays empty while the outage goes on.
    Unconfirmed rows (offline for fewer syncs than PBX_OUTAGE_CONFIRM_SYNCS) are never shown or logged
    and are deleted again if the phone comes back."""
    id = db.Column(db.Integer, primary_key=True)
    extension = db.Column(db.String(20), nullable=False, index=True)
    title = db.Column(db.String(150))  # snapshot: the extension may be renamed later
    last_ip = db.Column(db.String(45))
    started_at = db.Column(db.DateTime, nullable=False, index=True)
    ended_at = db.Column(db.DateTime, index=True)
    end_reason = db.Column(db.String(10))  # online = the phone came back; stopped = no longer monitored
    confirmed = db.Column(db.Boolean, default=False, nullable=False, index=True)
    offline_syncs = db.Column(db.Integer, default=1, nullable=False)

    @property
    def duration_seconds(self):
        end = self.ended_at or datetime.utcnow()
        return max(0, int((end - self.started_at).total_seconds()))


class OutageEvent(db.Model):
    """Something worth an alert: a phone's outage was confirmed ('offline') or ended because the phone
    came back ('online'). The browser polls these (see api_outage_alerts); old rows are purged.

    batch is the sync cycle that produced the event: events of one cycle count as simultaneous,
    which is what the summary alert ("12 phones went offline together") is built on."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(8), nullable=False)  # offline / online
    batch = db.Column(db.DateTime, nullable=False)
    extension = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(150))
    detail = db.Column(db.String(20))  # offline: local start time 'HH:MM'; online: how long it was down
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


def _ensure_columns(engine):
    """Lightweight in-place migration: add any columns that a pre-existing
    SQLite database might be missing, without touching existing rows."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if 'extension' not in inspector.get_table_names():
        return
    existing_ext_cols = {c['name'] for c in inspector.get_columns('extension')}
    existing_dept_cols = {c['name'] for c in inspector.get_columns('department')} if 'department' in inspector.get_table_names() else set()
    existing_user_cols = {c['name'] for c in inspector.get_columns('user')} if 'user' in inspector.get_table_names() else set()
    statements = []
    if 'email' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN email VARCHAR(150)')
    if 'mobile' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN mobile VARCHAR(30)')
    if 'is_present' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN is_present BOOLEAN DEFAULT 0 NOT NULL')
    if 'is_deleted' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN is_deleted BOOLEAN DEFAULT 0 NOT NULL')
    if 'deleted_at' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN deleted_at DATETIME')
    if 'working_hours' not in existing_dept_cols:
        statements.append('ALTER TABLE department ADD COLUMN working_hours VARCHAR(120)')
    if 'must_change_password' not in existing_user_cols:
        statements.append('ALTER TABLE user ADD COLUMN must_change_password BOOLEAN DEFAULT 0 NOT NULL')
    new_columns = (
        ('kind', "VARCHAR(10) DEFAULT 'staff' NOT NULL"),
        ('mac', 'VARCHAR(17)'),
        ('phone_model', 'VARCHAR(60)'),
        ('reg_status', 'VARCHAR(10)'),
        ('last_ip', 'VARCHAR(45)'),
        ('rtt_ms', 'INTEGER'),
        ('last_seen_at', 'DATETIME'),
        ('user_agent', 'VARCHAR(200)'),
        ('status_updated_at', 'DATETIME'),
    )
    for column_name, column_type in new_columns:
        if column_name not in existing_ext_cols:
            statements.append(f'ALTER TABLE extension ADD COLUMN {column_name} {column_type}')
    statements.append('CREATE INDEX IF NOT EXISTS ix_extension_kind ON extension (kind)')
    statements.append('CREATE INDEX IF NOT EXISTS ix_extension_mac ON extension (mac)')
    if statements:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))


# In-process "data version" counter: bumped on every admin write so the
# homepage/admin page can poll /api/version and detect changes without a
# full WebSocket stack. Also used to invalidate the tiny in-memory caches
# below. Note: this only works within a single process — a multi-worker
# deployment would need a shared store (e.g. Redis) instead.
_data_version = 0
_stats_cache = {'version': -1, 'data': None}
_departments_cache = {'version': -1, 'data': None}


def bump_data_version():
    global _data_version
    _data_version += 1


def log_activity(action, entity_type, entity_label, details=None):
    actor = current_user.display_name if getattr(current_user, 'is_authenticated', False) else 'system'
    db.session.add(ActivityLog(actor=actor, action=action, entity_type=entity_type, entity_label=entity_label, details=details))


def get_cached_stats():
    if _stats_cache['version'] != _data_version:
        _stats_cache['data'] = {
            'total_extensions': Extension.query.filter_by(is_deleted=False).count(),
            'total_departments': Department.query.count(),
            'active_count': Extension.query.filter_by(is_deleted=False, is_active=True).count(),
            'inactive_count': Extension.query.filter_by(is_deleted=False, is_active=False).count(),
            'last_updated': db.session.query(func.max(Extension.updated_at)).filter(Extension.is_deleted == False).scalar(),
            'admin_count': User.query.count(),
            'staff_count': Extension.query.filter_by(is_deleted=False, kind='staff').count(),
            'room_count': Extension.query.filter_by(is_deleted=False, kind='room').count(),
            'pending_count': Extension.query.filter_by(is_deleted=False, kind='pending').count(),
        }
        _stats_cache['version'] = _data_version
    return _stats_cache['data']


def get_cached_departments():
    """Only safe where callers read plain columns (name/working_hours) —
    do NOT use this where a lazy relationship like extension_entries is
    accessed, since the cached objects may be detached from a prior
    request's session."""
    if _departments_cache['version'] != _data_version:
        _departments_cache['data'] = Department.query.order_by(Department.name).all()
        _departments_cache['version'] = _data_version
    return _departments_cache['data']


def build_mac_plan(body, ext_idx, mac_idx, model_idx, overwrite=False, create_missing=False):
    """Dry-run of a MAC import: one dict per data row describing what WOULD happen."""
    existing = {e.extension: e for e in Extension.query.all()}
    owners = {}
    for e in existing.values():
        if e.mac and not e.is_deleted:
            owners.setdefault(e.mac, set()).add(e.extension)

    def cell(cells, idx):
        return cell_text(cells[idx]) if idx is not None and idx < len(cells) else ''

    plan, seen_ext = [], {}
    for row_no, cells in body:
        ext, raw_mac, model = cell(cells, ext_idx), cell(cells, mac_idx), cell(cells, model_idx)
        if not ext and not raw_mac:
            continue
        item = {'row': row_no, 'ext': ext, 'mac_raw': raw_mac, 'mac': None, 'model': model, 'action': '', 'note': ''}
        if not ext:
            item['action'] = 'skip_no_ext'
        elif not raw_mac:
            item['action'] = 'skip_empty_mac'
        else:
            mac = normalize_mac(raw_mac)
            item['mac'] = mac
            if not mac:
                item['action'] = 'invalid_mac'
            elif ext in seen_ext:
                item['action'] = 'skip_dup_ext'
                item['note'] = f'قبلاً در ردیف {seen_ext[ext]} آمده است'
            else:
                seen_ext[ext] = row_no
                entry = existing.get(ext)
                if entry is None:
                    item['action'] = 'create' if create_missing else 'skip_missing'
                    if create_missing:
                        item['note'] = 'نوع: ' + KIND_LABELS['room' if is_room_number(ext) else 'pending']
                elif entry.is_deleted:
                    item['action'] = 'skip_deleted'
                elif not entry.mac:
                    item['action'] = 'set'
                elif entry.mac == mac:
                    item['action'] = 'same'
                else:
                    item['action'] = 'overwrite' if overwrite else 'skip_conflict'
                    item['note'] = f'MAC فعلی: {entry.mac}'
        plan.append(item)

    file_owners = {}
    for item in plan:
        if item['mac'] and item['action'] in ('set', 'overwrite', 'create'):
            file_owners.setdefault(item['mac'], set()).add(item['ext'])
    for item in plan:
        if item['mac'] and item['action'] in ('set', 'overwrite', 'create', 'same'):
            others = (owners.get(item['mac'], set()) | file_owners.get(item['mac'], set())) - {item['ext']}
            if others:
                item['shared'] = True
                item['note'] = (item['note'] + ' — ' if item['note'] else '') + 'همین MAC روی داخلی ' + '، '.join(sorted(others)) + ' هم هست'
    return plan


def apply_mac_plan(plan, overwrite=False):
    """Write a plan produced by build_mac_plan to the database (caller commits)."""
    counts = {'set': 0, 'overwrite': 0, 'create': 0, 'model': 0}
    for item in plan:
        action = item['action']
        if action not in MAC_ACTIONS_APPLIED:
            continue
        entry = Extension.query.filter_by(extension=item['ext']).first()
        if action == 'create':
            kind = suggest_kind(item['ext'])
            entry = Extension(extension=item['ext'], title=('اتاق ' if kind == 'room' else 'داخلی ') + item['ext'],
                              kind='room' if kind == 'room' else 'pending', is_active=True)
            db.session.add(entry)
        if entry is None:
            continue
        if action in ('set', 'overwrite', 'create'):
            entry.mac = item['mac']
            counts[action] += 1
        if item['model'] and (action == 'create' or not entry.phone_model or overwrite):
            entry.phone_model = item['model'][:60]
            counts['model'] += 1
    return counts


# ---- PBX sync (Issabel / Asterisk, read-only) --------------------------------------------
# Two sources, same database columns:
#   ami - Asterisk Manager Interface; needs PBX_AMI_HOST, PBX_AMI_USER and PBX_AMI_SECRET
#   web - the Issabel web panel (form login + Operator Panel status stream); needs PBX_WEB_HOST,
#         PBX_WEB_USER and PBX_WEB_PASS. For servers where AMI cannot be set up.
# PBX_SOURCE=ami|web picks one; the default 'auto' uses AMI when it is configured, else the web
# panel. Off when neither is configured. Secrets only ever live in the environment. State is per
# process, so run a single worker (see DEPLOYMENT.md).
# Database mapping of the status fields: reg_status = online/offline/unknown, rtt_ms = latency in
# ms, status_updated_at = time of the last successful query.
PBX_EXTENSION_NAME = re.compile(r'^[0-9]{1,6}$')
pbx_sync_state = {'running': False, 'last_attempt': None, 'last_success': None, 'last_error': None, 'last_duration': None, 'summary': None,
                  'error_since': None}  # error_since: when the current streak of failed syncs began (for Log.txt)
pbx_sync_lock = threading.Lock()
_ua_cursor = {'offset': 0}


def pbx_config():
    def number(name, default, cast=int):
        try:
            return cast(os.getenv(name, default))
        except ValueError:
            return cast(default)

    def flag(name, default=False):
        raw = os.getenv(name, '').strip().lower()
        return default if not raw else raw in ('1', 'true', 'yes', 'on')

    host = os.getenv('PBX_AMI_HOST', '').strip()
    user = os.getenv('PBX_AMI_USER', '').strip()
    secret = os.getenv('PBX_AMI_SECRET', '')
    web_host = os.getenv('PBX_WEB_HOST', '').strip()
    web_user = os.getenv('PBX_WEB_USER', '').strip()
    web_pass = os.getenv('PBX_WEB_PASS', '')
    ami_ready = bool(host and user and secret)
    web_ready = bool(web_host and web_user and web_pass)
    wanted = os.getenv('PBX_SOURCE', 'auto').strip().lower()
    if wanted == 'web':
        source = 'web' if web_ready else None
    elif wanted == 'ami':
        source = 'ami' if ami_ready else None
    else:
        source = 'ami' if ami_ready else 'web' if web_ready else None
    return {
        'host': host, 'port': number('PBX_AMI_PORT', '5038'), 'user': user, 'secret': secret,
        'web_host': web_host, 'web_user': web_user, 'web_pass': web_pass,
        'web_verify_ssl': flag('PBX_VERIFY_SSL', False),  # Issabel's own certificate is self-signed
        'web_timeout': number('PBX_WEB_TIMEOUT', '10', float),
        'web_login_path': os.getenv('PBX_WEB_LOGIN_PATH', '').strip() or '/index.php',
        'web_peers_path': os.getenv('PBX_WEB_PEERS_PATH', '').strip() or '/index.php?menu=control_panel',
        'source': source,
        'interval': max(15, number('PBX_SYNC_INTERVAL', str(number('PBX_SYNC_INTERVAL_SECONDS', '30')))),
        'timeout': number('PBX_AMI_TIMEOUT', '5', float),
        'ua_batch': max(0, number('PBX_UA_BATCH', '60')),
        # Outage log: a phone must stay offline this many syncs in a row before it counts (blips are ignored)
        'outage_enabled': flag('PBX_OUTAGE_MONITOR', True),
        'outage_confirm_syncs': max(1, number('PBX_OUTAGE_CONFIRM_SYNCS', '2')),
        'outage_kinds': tuple(k for k in (p.strip() for p in os.getenv('PBX_OUTAGE_KINDS', 'staff,room').split(',')) if k in EXTENSION_KINDS) or ('staff', 'room'),
        # Browser alerts built from the outage events (needs the outage tracking)
        'alerts_enabled': source is not None and flag('PBX_OUTAGE_MONITOR', True) and flag('PBX_ALERTS_ENABLED', True),
        'alert_summary_threshold': max(1, number('PBX_ALERT_SUMMARY_THRESHOLD', '5')),  # more phones than this at once -> one summary alert
        'alert_on_recovery': flag('PBX_ALERT_ON_RECOVERY', True),  # quiet alert when a phone comes back
        'outage_keep_days': max(0, number('PBX_OUTAGE_KEEP_DAYS', '180')),  # 0 = never delete old outages
        'enabled': source is not None,
    }


OUTAGE_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def make_outage_tracker(cfg):
    """Tracker for one sync cycle, wired to the PhoneOutage table (call inside an app context)."""
    open_outages = {o.extension: o for o in PhoneOutage.query.filter(PhoneOutage.ended_at.is_(None)).all()}

    def create(**fields):
        record = PhoneOutage(**fields)
        db.session.add(record)
        return record

    def emit(kind, record, now):
        if not cfg['alerts_enabled']:
            return
        if kind == 'offline':
            detail = format_local_datetime(record.started_at, '%H:%M')
        else:
            detail = outage_monitor.format_duration((now - record.started_at).total_seconds())
        db.session.add(OutageEvent(kind=kind, batch=now, extension=record.extension, title=record.title, detail=detail))

    return outage_monitor.OutageTracker(open_outages, create, db.session.delete,
                                        lambda value: format_local_datetime(value, OUTAGE_TIME_FORMAT),
                                        cfg['outage_confirm_syncs'], emit)


OUTAGE_EVENT_KEEP_DAYS = 3          # alert events are only needed for a few minutes; keep them a little longer for debugging
OUTAGE_PURGE_EVERY = timedelta(hours=6)
OUTAGE_ALERT_MAX_AGE_MINUTES = 15   # a browser that was closed for longer than this is not told about old events
OUTAGE_PAGE_LIMIT = 500             # rows shown on the report page (the CSV has them all)
OUTAGE_CSV_LIMIT = 50000
_outage_purge_state = {'at': None}


def purge_old_outage_data(now, keep_days, force=False):
    """Delete finished outages older than keep_days (0 = keep everything) and old alert events.

    Throttled to once per OUTAGE_PURGE_EVERY unless forced; ongoing outages are never deleted.
    Returns how many outages were removed. Call inside an app context."""
    last = _outage_purge_state['at']
    if not force and last is not None and now - last < OUTAGE_PURGE_EVERY:
        return 0
    _outage_purge_state['at'] = now
    removed = 0
    if keep_days > 0:
        cutoff = now - timedelta(days=keep_days)
        removed = PhoneOutage.query.filter(PhoneOutage.ended_at.is_not(None), PhoneOutage.ended_at < cutoff).delete(synchronize_session=False)
    OutageEvent.query.filter(OutageEvent.created_at < now - timedelta(days=OUTAGE_EVENT_KEEP_DAYS)).delete(synchronize_session=False)
    db.session.commit()
    return removed


def note_pbx_failure(message):
    """Log (once per failure streak) that the PBX itself cannot be read; call before last_error is set."""
    if pbx_sync_state['last_error'] is None and pbx_config()['outage_enabled']:
        now = datetime.utcnow()
        pbx_sync_state['error_since'] = now
        outage_monitor.write_pbx_event('PBX-LOST', f'cannot read the PBX: {message}', format_local_datetime(now, OUTAGE_TIME_FORMAT))


def note_pbx_recovered():
    since = pbx_sync_state.get('error_since')
    pbx_sync_state['error_since'] = None
    if since is not None and pbx_config()['outage_enabled']:
        now = datetime.utcnow()
        lasted = outage_monitor.format_duration((now - since).total_seconds())
        outage_monitor.write_pbx_event('PBX-OK', f'reachable again after {lasted}', format_local_datetime(now, OUTAGE_TIME_FORMAT))


def sync_pbx_once(client_factory=None, now=None, web_scraper=None):
    """One read-only sync cycle PBX -> database; call it inside an app context.

    The peers come from AMI (client_factory) or, when AMI is not configured / PBX_SOURCE=web
    (or web_scraper is given), from the Issabel web panel; the web page has no User-Agent, so
    that source leaves phone model / MAC alone.

    Raises AMIError (PBXWebError for the web source) when the PBX cannot be read. Returns a summary dict.
    Live fields (status, IP, RTT, last seen, User-Agent) are refreshed for every
    numeric peer; peers that are new to us are created hidden from the public
    (room by number pattern, otherwise 'pending'); trashed extensions are left
    alone; a phone's model / MAC are filled from its User-Agent only when empty.
    updated_at is deliberately NOT touched: it keeps meaning 'last human edit'.

    Outages: a monitored phone (active, kind in PBX_OUTAGE_KINDS) that goes from online to offline is
    recorded in PhoneOutage and logged to Log.txt once it has stayed offline PBX_OUTAGE_CONFIRM_SYNCS
    syncs; the record is closed when it comes back. The log lines are written after the commit."""
    started = time.monotonic()
    now = now or datetime.utcnow()
    cfg = pbx_config()
    use_web = client_factory is None and (web_scraper is not None or cfg['source'] == 'web')
    if client_factory is None and not use_web:
        def client_factory():
            return AMIClient(cfg['host'], cfg['port'], cfg['user'], cfg['secret'], cfg['timeout'])

    entries = {e.extension: e for e in Extension.query.all()}  # includes trashed rows
    agents = {}
    if use_web:
        scraper = web_scraper or shared_scraper(cfg['web_host'], cfg['web_user'], cfg['web_pass'], cfg['web_verify_ssl'],
                                                cfg['web_timeout'], cfg['web_login_path'], cfg['web_peers_path'])
        peers = scraper.fetch_peer_states()
        numeric = {name: peer for name, peer in peers.items() if PBX_EXTENSION_NAME.match(name)}
    else:
        with client_factory() as client:
            peers = fetch_peers(client)
            numeric = {name: peer for name, peer in peers.items() if PBX_EXTENSION_NAME.match(name)}
            # Which User-Agents to (re)read this cycle: new, never-read or moved phones first,
            # then a rotating slice so firmware changes show up within a few minutes.
            online_names = sorted(name for name, peer in numeric.items() if peer.ip)
            wanted = [n for n in online_names if n not in entries or not entries[n].user_agent or entries[n].last_ip != numeric[n].ip]
            batch = cfg['ua_batch']
            targets = wanted[:batch]
            spare = batch - len(targets)
            if spare > 0:
                others = [n for n in online_names if n not in set(targets)]
                if others:
                    start = _ua_cursor['offset'] % len(others)
                    rotation = (others[start:] + others[:start])[:spare]
                    targets += rotation
                    _ua_cursor['offset'] = start + len(rotation)
            for name in targets:
                agents[name] = fetch_user_agent(client, name)

    created = in_trash = 0
    counts = {'online': 0, 'offline': 0, 'unknown': 0}
    monitor = make_outage_tracker(cfg) if cfg['outage_enabled'] else None
    observed = set()
    fresh = timedelta(minutes=PBX_STATUS_STALE_MINUTES)
    for name, peer in numeric.items():
        entry = entries.get(name)
        if entry is None:
            kind = suggest_kind(name)
            entry = Extension(extension=name, title=('اتاق ' if kind == 'room' else 'داخلی ') + name,
                              kind='room' if kind == 'room' else 'pending', is_active=True)
            db.session.add(entry)
            entries[name] = entry
            created += 1
        elif entry.is_deleted:
            in_trash += 1
            continue
        # the state of the previous sync, trusted only while it is recent (not after a long pause)
        prior = entry.reg_status if entry.status_updated_at and now - entry.status_updated_at <= fresh else None
        entry.reg_status = peer.state
        entry.rtt_ms = peer.rtt_ms if peer.state == 'online' else None
        if peer.ip:
            entry.last_ip = peer.ip
        if peer.state == 'online':
            entry.last_seen_at = now
        entry.status_updated_at = now
        user_agent = agents.get(name)
        if user_agent:
            entry.user_agent = user_agent[:200]
            info = parse_user_agent(user_agent)
            if not entry.phone_model and info['display_model']:
                entry.phone_model = info['display_model'][:60]
            if not entry.mac and info['mac']:
                entry.mac = info['mac']
        counts[peer.state] += 1
        if monitor is not None and entry.is_active and entry.kind in cfg['outage_kinds']:
            observed.add(name)
            monitor.observe(name, peer.state, prior, entry.title, entry.last_ip, now)

    not_on_pbx = 0
    for name, entry in entries.items():
        if entry.is_deleted or name in numeric:
            continue
        not_on_pbx += 1
        if entry.reg_status or entry.status_updated_at:  # no longer on the PBX: drop the stale live state
            entry.reg_status = None
            entry.rtt_ms = None
            entry.status_updated_at = None
    for entry in list(db.session.dirty):
        if isinstance(entry, Extension) and entry.id is not None:
            flag_modified(entry, 'updated_at')  # keep the old updated_at: onupdate must not fire for sync writes
    if monitor is not None and numeric:  # an empty answer must never close every outage at once
        monitor.stop_unobserved(observed, now)
    if created:
        log_activity('created', 'extension', f'همگام‌سازی PBX: {created} داخلی جدید', details='ساخته‌شده از روی ایزابل؛ مخفی از پرسنل تا تأیید مدیر')
        bump_data_version()
    db.session.commit()
    if monitor is not None:
        monitor.write_log()  # only now: the file must never describe something the database does not hold
        try:
            purge_old_outage_data(now, cfg['outage_keep_days'])
        except Exception:  # housekeeping must never make a good sync look failed
            db.session.rollback()
            logging.getLogger('hotel.purge').exception('purging old outage records failed')
    return {'peers': len(peers), 'extensions': len(numeric), 'ignored': len(peers) - len(numeric), 'created': created,
            'in_trash': in_trash, 'not_on_pbx': not_on_pbx, 'user_agents': len(agents), 'seconds': round(time.monotonic() - started, 2),
            'went_offline': monitor.went_offline if monitor else 0, 'came_online': monitor.came_online if monitor else 0, **counts}


def run_pbx_sync(app_obj, client_factory=None):
    """Run one cycle with locking and state bookkeeping -> (ok, summary dict or error text)."""
    if not pbx_sync_lock.acquire(blocking=False):
        return False, 'یک همگام‌سازی دیگر در حال اجراست.'
    try:
        started = time.monotonic()
        pbx_sync_state['running'] = True
        pbx_sync_state['last_attempt'] = datetime.utcnow()
        with app_obj.app_context():
            try:
                summary = sync_pbx_once(client_factory)
            except (AMIError, OSError) as exc:
                db.session.rollback()
                message = str(exc)
                note_pbx_failure(message)
                if message != pbx_sync_state['last_error']:
                    app_obj.logger.warning('PBX sync failed: %s', message)
                pbx_sync_state['last_error'] = message
                return False, message
            except Exception:
                db.session.rollback()
                app_obj.logger.exception('PBX sync crashed')
                note_pbx_failure('internal error while syncing; see app.log')
                pbx_sync_state['last_error'] = 'خطای داخلی در همگام‌سازی؛ جزئیات در لاگ سرور ثبت شد.'
                return False, pbx_sync_state['last_error']
        if pbx_sync_state['last_error']:
            app_obj.logger.info('PBX sync recovered after: %s', pbx_sync_state['last_error'])
            note_pbx_recovered()
        pbx_sync_state.update(last_success=datetime.utcnow(), last_error=None, summary=summary, last_duration=time.monotonic() - started)
        return True, summary
    finally:
        pbx_sync_state['running'] = False
        pbx_sync_lock.release()


def start_pbx_sync_thread(app_obj):
    """Background polling thread (daemon). Returns None when the PBX link is not configured."""
    if not pbx_config()['enabled']:
        return None
    stop = threading.Event()

    def loop():
        while True:
            run_pbx_sync(app_obj)
            if stop.wait(pbx_config()['interval']):
                break

    thread = threading.Thread(target=loop, name='pbx-sync', daemon=True)
    thread.start()
    app_obj.extensions['pbx_sync_stop'] = stop
    return thread


def create_app():
    app = Flask(__name__)
    admin_prefix = get_or_create_admin_prefix()
    app.config.update(
        SECRET_KEY=get_or_create_secret_key(),
        SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(BASE_DIR, 'hotel_extensions.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        PDF_FONT_PATH=os.getenv('PDF_FONT_PATH', ''),  # empty = auto-detect, see resolve_pdf_font()
        SEND_FILE_MAX_AGE_DEFAULT=1800,  # cache static assets (CSS/JS) in the browser for 30 min
        ADMIN_URL_PREFIX=admin_prefix,
        # Staff e-mail / mobile numbers are shown to logged-in admins only,
        # unless SHOW_CONTACT_PUBLICLY=1 is set in the environment.
        SHOW_CONTACT_PUBLICLY=os.getenv('SHOW_CONTACT_PUBLICLY', '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=int(os.getenv('SESSION_IDLE_MINUTES', '60'))),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        # Set SESSION_COOKIE_SECURE=1 once the site is served over HTTPS.
        SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', '0') == '1',
    )
    # Trust X-Forwarded-* headers from exactly one reverse proxy hop, so
    # request.remote_addr (used for login rate-limiting and activity
    # logging) reflects the real client — but only when the request really
    # arrives from a trusted proxy (see TrustedProxyFix / TRUSTED_PROXIES).
    trusted_proxies = [p.strip() for p in os.getenv('TRUSTED_PROXIES', '127.0.0.1,::1').split(',') if p.strip()]
    app.wsgi_app = TrustedProxyFix(app.wsgi_app, trusted_proxies)
    db.init_app(app)
    login_manager.init_app(app)
    CSRFProtect(app)
    app.jinja_env.globals['dept_icon'] = department_icon
    def can_see_contact():
        return app.config['SHOW_CONTACT_PUBLICLY'] or current_user.is_authenticated

    app.jinja_env.globals['can_see_contact'] = can_see_contact
    app.jinja_env.globals['phone_status'] = display_phone_status
    app.jinja_env.globals['phone_status_labels'] = PHONE_STATUS_LABELS
    app.jinja_env.globals['phone_slow'] = phone_is_slow
    app.jinja_env.globals['phone_label'] = phone_label
    app.jinja_env.globals['kind_labels'] = KIND_LABELS
    app.jinja_env.globals['pbx_config'] = pbx_config
    app.jinja_env.globals['pbx_state'] = pbx_sync_state
    app.jinja_env.globals['data_version'] = lambda: _data_version
    app.jinja_env.filters['local_dt'] = format_local_datetime
    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)

    @app.before_request
    def enforce_idle_timeout():
        # Sliding session: every request resets the expiry clock, but if
        # the browser sat idle past PERMANENT_SESSION_LIFETIME the cookie
        # itself will already have expired — this just makes the session
        # non-permanent-by-default explicit and refreshes the timer.
        session.permanent = True

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        return response

    if not app.debug:
        log_dir = os.path.join(BASE_DIR, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(os.path.join(log_dir, 'app.log'), maxBytes=1_000_000, backupCount=5, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)

    # Log.txt: one line per phone outage (went offline / came back / how long); PBX_OUTAGE_LOG moves it
    outage_monitor.setup_log_file(os.getenv('PBX_OUTAGE_LOG', '').strip() or os.path.join(BASE_DIR, 'logs', outage_monitor.LOG_FILE_NAME))

    @app.route('/robots.txt')
    def robots_txt():
        return 'User-agent: *\nDisallow: /\n', 200, {'Content-Type': 'text/plain; charset=utf-8'}

    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        flash('نشست شما منقضی شده یا صفحه در تب دیگری قدیمی بوده است. لطفاً دوباره تلاش کنید.', 'danger')
        return redirect(request.referrer or url_for('index')), 303

    @app.errorhandler(404)
    def not_found(_error):
        return render_template('error.html', code=404, heading='صفحه پیدا نشد', message='آدرسی که وارد کردید وجود ندارد.'), 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception('Unhandled server error: %s', error)
        return render_template('error.html', code=500, heading='خطای داخلی سرور', message='مشکلی پیش آمد. لطفاً کمی بعد دوباره تلاش کنید.'), 500

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    with app.app_context():
        db.create_all()
        _ensure_columns(db.engine)
        seed_data()
        flag_default_passwords(app)

    @app.before_request
    def enforce_password_change():
        if current_user.is_authenticated and getattr(current_user, 'must_change_password', False):
            allowed = {'change_password', 'logout', 'static'}
            if request.endpoint not in allowed:
                return redirect(url_for('change_password'))

    @app.route('/api/version')
    def api_version():
        return {'version': _data_version}

    def build_filtered_query(args):
        """Shared filter/sort logic for the public extension list, reused
        by the homepage and by the export routes so 'export' always
        matches whatever is currently on screen."""
        query = args.get('q', '').strip()
        department_id = args.get('department', type=int)
        status = args.get('status', '')
        ext_q = args.get('ext', '').strip()
        person_q = args.get('person', '').strip()
        title_q = args.get('title_q', '').strip()
        location_q = args.get('location_q', '').strip()

        sort_map = {
            'extension': Extension.extension,
            'title': Extension.title,
            'department': Department.name,
            'location': Extension.location,
            'created': Extension.created_at,
            'updated': Extension.updated_at,
        }
        sort_value = args.get('sort', 'extension:asc')
        sort_field, _, sort_dir = sort_value.partition(':')
        if sort_field not in sort_map:
            sort_field = 'extension'
        if sort_dir not in ('asc', 'desc'):
            sort_dir = 'asc'
        sort_value = f'{sort_field}:{sort_dir}'

        # Public list: staff/office extensions only. Guest-room and unreviewed
        # extensions must never reach the public page, search, PDF or exports.
        entries = Extension.query.filter_by(is_deleted=False, kind='staff')
        if query:
            pattern = f'%{query}%'
            conditions = [Extension.extension.ilike(pattern), Extension.title.ilike(pattern), Extension.person_name.ilike(pattern), Extension.location.ilike(pattern)]
            matching_dept_ids = [d.id for d in Department.query.filter(Department.name.ilike(pattern)).all()]
            if matching_dept_ids:
                conditions.append(Extension.department_id.in_(matching_dept_ids))
            entries = entries.filter(or_(*conditions))
        if department_id:
            entries = entries.filter_by(department_id=department_id)
        if status == 'active':
            entries = entries.filter_by(is_active=True)
        elif status == 'inactive':
            entries = entries.filter_by(is_active=False)
        if ext_q:
            entries = entries.filter(Extension.extension.ilike(f'%{ext_q}%'))
        if person_q:
            entries = entries.filter(Extension.person_name.ilike(f'%{person_q}%'))
        if title_q:
            entries = entries.filter(Extension.title.ilike(f'%{title_q}%'))
        if location_q:
            entries = entries.filter(Extension.location.ilike(f'%{location_q}%'))

        if sort_field == 'department':
            entries = entries.outerjoin(Department, Extension.department_id == Department.id)

        sort_column = sort_map[sort_field]
        entries = entries.order_by(sort_column.desc() if sort_dir == 'desc' else sort_column.asc())

        filters_active = bool(query or department_id or status or ext_q or person_q or title_q or location_q)
        return entries, sort_value, filters_active

    @app.route('/')
    def index():
        per_page_raw = request.args.get('per_page', '20').strip().lower()
        page = request.args.get('page', 1, type=int)
        if page < 1:
            page = 1

        entries, sort_value, _ = build_filtered_query(request.args)
        total_count = entries.count()

        show_all = per_page_raw == 'all'
        allowed_per_page = (20, 50, 100)
        per_page = 20 if show_all else per_page_raw
        if not show_all:
            per_page = per_page_raw if per_page_raw.isdigit() and int(per_page_raw) in allowed_per_page else 20
            per_page = int(per_page)

        if show_all:
            total_pages = 1
            page = 1
            page_entries = entries.all()
        else:
            total_pages = max(1, (total_count + per_page - 1) // per_page)
            page = min(page, total_pages)
            page_entries = entries.offset((page - 1) * per_page).limit(per_page).all()

        return render_template(
            'index.html',
            entries=page_entries,
            show_phone_status=any(display_phone_status(e) for e in page_entries),
            departments=get_cached_departments(),
            query=request.args.get('q', '').strip(),
            selected_department=request.args.get('department', type=int),
            status=request.args.get('status', ''),
            ext_q=request.args.get('ext', '').strip(),
            person_q=request.args.get('person', '').strip(),
            title_q=request.args.get('title_q', '').strip(),
            location_q=request.args.get('location_q', '').strip(),
            sort_value=sort_value,
            per_page=('all' if show_all else per_page),
            page=page,
            total_pages=total_pages,
            total_count=total_count,
        )

    @app.route(f'/{admin_prefix}')
    @login_required
    def admin():
        q = request.args.get('q', '').strip()
        kind_filter = request.args.get('kind', '')
        if kind_filter not in EXTENSION_KINDS:
            kind_filter = ''
        entries_query = Extension.query.filter_by(is_deleted=False)
        if kind_filter:
            entries_query = entries_query.filter_by(kind=kind_filter)
        if q:
            pattern = f'%{q}%'
            conditions = [Extension.extension.ilike(pattern), Extension.title.ilike(pattern), Extension.person_name.ilike(pattern), Extension.location.ilike(pattern), Extension.last_ip.ilike(pattern)]
            mac_digits = re.sub(r'[^0-9a-fA-F]', '', q)
            if len(mac_digits) >= 4:  # admins can search by MAC with or without separators
                conditions.append(func.replace(Extension.mac, ':', '').ilike(f'%{mac_digits}%'))
            entries_query = entries_query.filter(or_(*conditions))
        entries = entries_query.order_by(Extension.extension).all()
        return render_template(
            'admin.html',
            entries=entries,
            departments=Department.query.order_by(Department.name).all(),
            q=q,
            kind_filter=kind_filter,
            import_stats=session.get('import_stats'),
            import_filename=session.get('import_original_name'),
            stats=get_cached_stats(),
        )

    @app.route(f'/{admin_prefix}/activity-log')
    @login_required
    def activity_log():
        logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(300).all()
        return render_template('activity_log.html', logs=logs)

    @app.route(f'/{admin_prefix}/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('admin'))
        if request.method == 'POST':
            ip = request.remote_addr or 'unknown'
            if is_login_rate_limited(ip):
                flash('تعداد تلاش‌های ناموفق بیش از حد مجاز است. چند دقیقه دیگر دوباره امتحان کنید.', 'danger')
                return render_template('login.html')
            user = User.query.filter_by(username=request.form.get('username', '').strip()).first()
            if user and user.check_password(request.form.get('password', '')):
                clear_login_failures(ip)
                login_user(user)
                if user.must_change_password:
                    flash('برای ادامه، ابتدا رمز عبور خود را تغییر دهید.', 'warning')
                    return redirect(url_for('change_password'))
                return redirect(url_for('admin'))
            record_login_failure(ip)
            flash('نام کاربری یا رمز عبور صحیح نیست.', 'danger')
        return render_template('login.html')

    @app.route('/logout', methods=['POST'])
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('index'))

    @app.route(f'/{admin_prefix}/change-password', methods=['GET', 'POST'])
    @login_required
    def change_password():
        if request.method == 'POST':
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')
            if not current_user.check_password(current_password):
                flash('رمز عبور فعلی صحیح نیست.', 'danger')
            elif len(new_password) < 6:
                flash('رمز عبور جدید باید حداقل ۶ کاراکتر باشد.', 'danger')
            elif new_password in DEFAULT_PASSWORDS:
                flash('این رمز عبور پیش‌فرض و قابل حدس است؛ رمز دیگری انتخاب کنید.', 'danger')
            elif new_password != confirm_password:
                flash('رمز عبور جدید و تکرار آن یکسان نیستند.', 'danger')
            else:
                current_user.set_password(new_password)
                current_user.must_change_password = False
                log_activity('updated', 'user', f'تغییر رمز عبور — {current_user.username}')
                db.session.commit()
                flash('رمز عبور با موفقیت تغییر کرد.', 'success')
                return redirect(url_for('admin'))
        return render_template('change_password.html', forced=current_user.must_change_password)

    @app.route(f'/{admin_prefix}/users')
    @login_required
    def admin_users():
        users = User.query.order_by(User.username).all()
        return render_template('admin_users.html', users=users)

    @app.route(f'/{admin_prefix}/users', methods=['POST'])
    @login_required
    def create_user():
        username = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        password = request.form.get('password', '')
        if not username or not display_name or len(password) < 6:
            flash('نام کاربری، نام نمایشی و رمز عبور (حداقل ۶ کاراکتر) الزامی هستند.', 'danger')
        elif User.query.filter_by(username=username).first():
            flash('این نام کاربری قبلاً ثبت شده است.', 'danger')
        else:
            user = User(username=username, display_name=display_name, must_change_password=True)
            user.set_password(password)
            db.session.add(user)
            log_activity('created', 'user', f'{username} ({display_name})')
            db.session.commit()
            flash('مدیر جدید افزوده شد. در اولین ورود باید رمز عبور را تغییر دهد.', 'success')
        return redirect(url_for('admin_users'))

    @app.route(f'/{admin_prefix}/users/<int:user_id>/delete', methods=['POST'])
    @login_required
    def delete_user(user_id):
        target = db.get_or_404(User, user_id)
        if target.id == current_user.id:
            flash('نمی‌توانید حساب کاربری خودتان را حذف کنید.', 'danger')
        elif User.query.count() <= 1:
            flash('حداقل یک مدیر باید در سامانه باقی بماند.', 'danger')
        else:
            log_activity('deleted', 'user', f'{target.username} ({target.display_name})')
            db.session.delete(target)
            db.session.commit()
            flash('مدیر حذف شد.', 'success')
        return redirect(url_for('admin_users'))

    @app.route(f'/{admin_prefix}/extensions/new', methods=['GET', 'POST'])
    @login_required
    def create_extension():
        if request.method == 'POST':
            return save_extension()
        return render_template('extension_form.html', entry=None, departments=Department.query.order_by(Department.name).all())

    @app.route(f'/{admin_prefix}/extensions/<int:entry_id>/edit', methods=['GET', 'POST'])
    @login_required
    def edit_extension(entry_id):
        entry = db.get_or_404(Extension, entry_id)
        if request.method == 'POST':
            return save_extension(entry)
        return render_template('extension_form.html', entry=entry, departments=Department.query.order_by(Department.name).all())

    def save_extension(entry=None):
        extension_no = request.form.get('extension', '').strip()
        title = request.form.get('title', '').strip()
        if not extension_no or not title:
            flash('شماره داخلی و عنوان الزامی هستند.', 'danger')
            return redirect(request.url)
        duplicate = Extension.query.filter(Extension.extension == extension_no)
        if entry:
            duplicate = duplicate.filter(Extension.id != entry.id)
        duplicate = duplicate.first()
        if duplicate:
            if duplicate.is_deleted:
                flash('این شماره داخلی در سطل بازیافت وجود دارد. برای استفاده مجدد از آن، ابتدا از سطل بازیافت بازگردانید یا برای همیشه حذف کنید.', 'danger')
            else:
                flash('این شماره داخلی قبلاً ثبت شده است.', 'danger')
            return redirect(request.url)
        mac_raw = request.form.get('mac', '').strip()
        mac = normalize_mac(mac_raw) if mac_raw else None
        if mac_raw and not mac:
            flash('MAC نامعتبر است. مثال درست: 00:15:65:AA:BB:CC', 'danger')
            return redirect(request.url)
        kind_choice = request.form.get('kind', 'auto')
        kind = kind_choice if kind_choice in EXTENSION_KINDS else suggest_kind(extension_no)
        is_new = entry is None
        entry = entry or Extension()
        entry.extension = extension_no
        entry.title = title
        entry.person_name = request.form.get('person_name', '').strip() or None
        entry.department_id = request.form.get('department_id', type=int) or None
        entry.location = request.form.get('location', '').strip() or None
        entry.email = request.form.get('email', '').strip() or None
        entry.mobile = request.form.get('mobile', '').strip() or None
        entry.notes = request.form.get('notes', '').strip() or None
        entry.is_active = request.form.get('is_active') == 'on'
        entry.is_present = request.form.get('is_present') == 'on'
        entry.kind = kind
        entry.mac = mac
        entry.phone_model = request.form.get('phone_model', '').strip()[:60] or None
        db.session.add(entry)
        log_activity('created' if is_new else 'updated', 'extension', f'{extension_no} — {title}')
        bump_data_version()
        db.session.commit()
        flash('داخلی با موفقیت ذخیره شد.', 'success')
        if kind != 'staff':
            flash(f'نوع این داخلی «{KIND_LABELS[kind]}» است و برای پرسنل نمایش داده نمی‌شود. اگر داخلی اداری است، نوع را «پرسنل / اداری» بگذارید.', 'warning')
        if mac:
            shared = Extension.query.filter(Extension.mac == mac, Extension.id != entry.id, Extension.is_deleted == False).all()
            if shared:
                flash('همین MAC روی داخلی ' + '، '.join(x.extension for x in shared) + ' هم ثبت شده است. اگر یک تلفن چند خط دارد مشکلی نیست؛ در غیر این‌صورت بررسی کنید.', 'warning')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/extensions/<int:entry_id>/delete', methods=['POST'])
    @login_required
    def delete_extension(entry_id):
        entry = db.get_or_404(Extension, entry_id)
        entry.is_deleted = True
        entry.deleted_at = datetime.utcnow()
        log_activity('deleted', 'extension', f'{entry.extension} — {entry.title}', details='به سطل بازیافت منتقل شد')
        bump_data_version()
        db.session.commit()
        flash('داخلی به سطل بازیافت منتقل شد.', 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/extensions/delete-all', methods=['POST'])
    @login_required
    def delete_all_extensions():
        if request.form.get('confirmation', '').strip() != 'پاک کن':
            flash('برای پاک‌سازی، عبارت «پاک کن» را دقیق وارد کنید.', 'danger')
            return redirect(url_for('admin'))
        count = Extension.query.filter_by(is_deleted=False).update({Extension.is_deleted: True, Extension.deleted_at: datetime.utcnow()}, synchronize_session=False)
        log_activity('deleted', 'extension', f'پاک‌سازی گروهی ({count} داخلی)', details='به سطل بازیافت منتقل شد')
        bump_data_version()
        db.session.commit()
        flash(f'{count} داخلی به سطل بازیافت منتقل شد.', 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/extensions/<int:entry_id>/restore', methods=['POST'])
    @login_required
    def restore_extension(entry_id):
        entry = db.get_or_404(Extension, entry_id)
        entry.is_deleted = False
        entry.deleted_at = None
        log_activity('updated', 'extension', f'{entry.extension} — {entry.title}', details='از سطل بازیافت بازگردانده شد')
        bump_data_version()
        db.session.commit()
        flash('داخلی بازگردانده شد.', 'success')
        return redirect(url_for('trash'))

    @app.route(f'/{admin_prefix}/extensions/<int:entry_id>/purge', methods=['POST'])
    @login_required
    def purge_extension(entry_id):
        entry = db.get_or_404(Extension, entry_id)
        log_activity('deleted', 'extension', f'{entry.extension} — {entry.title}', details='حذف همیشگی')
        db.session.delete(entry)
        bump_data_version()
        db.session.commit()
        flash('داخلی برای همیشه حذف شد.', 'success')
        return redirect(url_for('trash'))

    @app.route(f'/{admin_prefix}/trash')
    @login_required
    def trash():
        deleted_entries = Extension.query.filter_by(is_deleted=True).order_by(Extension.deleted_at.desc()).all()
        return render_template('trash.html', entries=deleted_entries)

    @app.route(f'/{admin_prefix}/extensions/bulk-activate', methods=['POST'])
    @login_required
    def bulk_activate():
        ids = request.form.getlist('selected_ids', type=int)
        if not ids:
            flash('هیچ داخلی‌ای انتخاب نشده بود.', 'danger')
        else:
            Extension.query.filter(Extension.id.in_(ids)).update({Extension.is_active: True}, synchronize_session=False)
            log_activity('updated', 'extension', f'فعال‌سازی گروهی ({len(ids)} داخلی)')
            bump_data_version()
            db.session.commit()
            flash(f'{len(ids)} داخلی فعال شد.', 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/extensions/bulk-deactivate', methods=['POST'])
    @login_required
    def bulk_deactivate():
        ids = request.form.getlist('selected_ids', type=int)
        if not ids:
            flash('هیچ داخلی‌ای انتخاب نشده بود.', 'danger')
        else:
            Extension.query.filter(Extension.id.in_(ids)).update({Extension.is_active: False}, synchronize_session=False)
            log_activity('updated', 'extension', f'غیرفعال‌سازی گروهی ({len(ids)} داخلی)')
            bump_data_version()
            db.session.commit()
            flash(f'{len(ids)} داخلی غیرفعال شد.', 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/extensions/bulk-delete', methods=['POST'])
    @login_required
    def bulk_delete():
        ids = request.form.getlist('selected_ids', type=int)
        if not ids:
            flash('هیچ داخلی‌ای انتخاب نشده بود.', 'danger')
        else:
            Extension.query.filter(Extension.id.in_(ids)).update({Extension.is_deleted: True, Extension.deleted_at: datetime.utcnow()}, synchronize_session=False)
            log_activity('deleted', 'extension', f'انتقال گروهی به سطل بازیافت ({len(ids)} داخلی)')
            bump_data_version()
            db.session.commit()
            flash(f'{len(ids)} داخلی به سطل بازیافت منتقل شد.', 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/departments', methods=['POST'])
    @login_required
    def create_department():
        name = request.form.get('name', '').strip()
        working_hours = request.form.get('working_hours', '').strip() or None
        if name and not Department.query.filter_by(name=name).first():
            db.session.add(Department(name=name, working_hours=working_hours))
            log_activity('created', 'department', name)
            bump_data_version()
            db.session.commit()
            flash('بخش جدید افزوده شد.', 'success')
        else:
            flash('نام بخش خالی یا تکراری است.', 'danger')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/departments/<int:department_id>/edit', methods=['POST'])
    @login_required
    def edit_department(department_id):
        department = db.get_or_404(Department, department_id)
        name = request.form.get('name', '').strip()
        working_hours = request.form.get('working_hours', '').strip() or None
        if not name:
            flash('نام بخش نمی‌تواند خالی باشد.', 'danger')
        elif Department.query.filter(Department.name == name, Department.id != department.id).first():
            flash('بخشی با این نام از قبل وجود دارد.', 'danger')
        else:
            department.name = name
            department.working_hours = working_hours
            log_activity('updated', 'department', name)
            bump_data_version()
            db.session.commit()
            flash('بخش ویرایش شد.', 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/departments/<int:department_id>/delete', methods=['POST'])
    @login_required
    def delete_department(department_id):
        department = db.get_or_404(Department, department_id)
        Extension.query.filter_by(department_id=department.id).update({Extension.department_id: None}, synchronize_session=False)
        log_activity('deleted', 'department', department.name)
        db.session.delete(department)
        bump_data_version()
        db.session.commit()
        flash('بخش حذف شد؛ داخلی‌های آن بدون بخش باقی مانده‌اند.', 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/backup')
    @login_required
    def download_backup():
        db_path = os.path.join(BASE_DIR, 'hotel_extensions.db')
        if not os.path.isfile(db_path):
            flash('فایل دیتابیس پیدا نشد.', 'danger')
            return redirect(url_for('admin'))
        stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        try:
            snapshot = make_db_backup(db_path)
        except (sqlite3.Error, OSError):
            app.logger.exception('Database backup failed')
            flash('ساخت نسخهٔ پشتیبان ناموفق بود. جزئیات در لاگ سرور ثبت شد.', 'danger')
            return redirect(url_for('admin'))
        log_activity('exported', 'backup', 'دانلود نسخه پشتیبان کامل دیتابیس')
        db.session.commit()
        return send_file(snapshot, mimetype='application/octet-stream', as_attachment=True, download_name=f'hotel_extensions_backup_{stamp}.db')

    @app.route(f'/{admin_prefix}/export/<fmt>')
    @login_required
    def export_data(fmt):
        entries = Extension.query.filter_by(is_deleted=False).order_by(Extension.extension).all()
        response = build_tabular_export(fmt, entries, include_contact=True, include_network=True)
        if response is None:
            return 'فرمت نامعتبر است.', 404
        return response

    @app.route('/export/<fmt>')
    def public_export(fmt):
        if fmt not in ('csv', 'xlsx'):
            return 'فرمت نامعتبر است.', 404
        entries, _, _ = build_filtered_query(request.args)
        response = build_tabular_export(fmt, entries.all(), include_contact=can_see_contact())
        if response is None:
            return 'فرمت نامعتبر است.', 404
        return response

    @app.route('/export/pdf')
    def public_export_pdf():
        entries, _, filters_active = build_filtered_query(request.args)
        entries = entries.all()
        if not entries:
            flash('هیچ داخلی‌ای برای این جست‌وجو/فیلتر برای خروجی گرفتن پیدا نشد.' if filters_active else 'هنوز داخلی‌ای برای خروجی گرفتن ثبت نشده است.', 'danger')
            return redirect(url_for('index'))
        font_path, font_error = resolve_pdf_font(app.config['PDF_FONT_PATH'])
        if font_error:
            flash(font_error, 'danger')
            return redirect(url_for('index'))
        output = io.BytesIO()
        build_extensions_pdf(output, entries, font_path)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='hotel_extensions.pdf')

    @app.route(f'/{admin_prefix}/export/pdf')
    @login_required
    def export_pdf():
        department_id = request.args.get('department_id', type=int)
        # The printed directory lists staff/office extensions only (no guest rooms).
        entries_query = Extension.query.filter_by(is_deleted=False, kind='staff')
        if department_id:
            entries_query = entries_query.filter_by(department_id=department_id)
        entries = entries_query.order_by(Extension.extension).all()
        if not entries:
            flash('داخلی‌ای برای بخش انتخاب‌شده پیدا نشد.', 'danger')
            return redirect(url_for('admin'))
        font_path, font_error = resolve_pdf_font(app.config['PDF_FONT_PATH'])
        if font_error:
            flash(font_error, 'danger')
            return redirect(url_for('admin'))
        output = io.BytesIO()
        build_extensions_pdf(output, entries, font_path)
        output.seek(0)
        name = 'hotel_extensions_all_departments.pdf' if not department_id else f'hotel_extensions_department_{department_id}.pdf'
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name=name)

    @app.route(f'/{admin_prefix}/import/preview', methods=['POST'])
    @login_required
    def import_preview():
        _cleanup_stale_imports()
        file = request.files.get('file')
        if not file or not file.filename:
            flash('فایلی انتخاب نشده است.', 'danger')
            return redirect(url_for('admin'))
        if not file.filename.lower().endswith(('.csv', '.xlsx')):
            flash('فقط فایل CSV یا XLSX پذیرفته می‌شود.', 'danger')
            return redirect(url_for('admin'))
        try:
            rows = read_import_file(file)
        except Exception:
            flash('فایل قابل خواندن نیست. ستون‌های استاندارد را بررسی کنید.', 'danger')
            return redirect(url_for('admin'))

        total = len(rows)
        valid = 0
        errors = 0
        duplicates_in_file = 0
        existing_updates = 0
        hidden = 0
        warnings = 0
        seen_in_file = set()
        for row in rows:
            extension_no = str(row.get('extension', '')).strip()
            title = str(row.get('title', '')).strip()
            if not extension_no or not title:
                errors += 1
                continue
            valid += 1
            if extension_no in seen_in_file:
                duplicates_in_file += 1
            else:
                seen_in_file.add(extension_no)
            existing = Extension.query.filter_by(extension=extension_no).first()
            if existing:
                existing_updates += 1
            extras, invalid_fields = parse_row_extras(row)
            warnings += len(invalid_fields)
            final_kind = extras.get('kind') or (existing.kind if existing else suggest_kind(extension_no))
            if final_kind != 'staff':
                hidden += 1

        token = uuid.uuid4().hex
        file_ext = os.path.splitext(file.filename)[1] or '.csv'
        temp_path = os.path.join(IMPORT_TMP_DIR, f'{token}{file_ext}')
        file.stream.seek(0)
        file.save(temp_path)

        session['import_token'] = token
        session['import_original_name'] = file.filename
        session['import_stats'] = {
            'total': total,
            'valid': valid,
            'errors': errors,
            'duplicates': duplicates_in_file,
            'existing_updates': existing_updates,
            'new_records': valid - duplicates_in_file - existing_updates,
            'hidden': hidden,
            'warnings': warnings,
        }
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/import/confirm', methods=['POST'])
    @login_required
    def import_confirm():
        token = session.pop('import_token', None)
        session.pop('import_original_name', None)
        session.pop('import_stats', None)
        if not token:
            flash('پیش‌نمایشی برای تأیید پیدا نشد؛ دوباره فایل را انتخاب کنید.', 'danger')
            return redirect(url_for('admin'))
        matches = glob.glob(os.path.join(IMPORT_TMP_DIR, f'{token}.*'))
        if not matches:
            flash('فایل موقت پیدا نشد؛ دوباره تلاش کنید.', 'danger')
            return redirect(url_for('admin'))
        temp_path = matches[0]
        try:
            with open(temp_path, 'rb') as raw_stream:
                storage = FileStorage(stream=raw_stream, filename=os.path.basename(temp_path))
                rows = read_import_file(storage)
                saved = 0
                for row in rows:
                    extension_no = str(row.get('extension', '')).strip()
                    title = str(row.get('title', '')).strip()
                    if not extension_no or not title:
                        continue
                    department_name = str(row.get('department', '')).strip()
                    department = get_or_create_department(department_name) if department_name else None
                    existing = Extension.query.filter_by(extension=extension_no).first()
                    item = existing or Extension(extension=extension_no)
                    item.title = title
                    item.person_name = str(row.get('person_name', '')).strip() or None
                    item.department = department
                    item.location = str(row.get('location', '')).strip() or None
                    item.notes = str(row.get('notes', '')).strip() or None
                    # Optional columns: only touched when the file has them, so older
                    # files (without these columns) never wipe existing data.
                    if 'email' in row:
                        item.email = cell_text(row.get('email')) or None
                    if 'mobile' in row:
                        item.mobile = cell_text(row.get('mobile')) or None
                    if 'present' in row:
                        item.is_present = parse_present(row.get('present'))
                    # kind / mac / phone_model: blank or invalid cells leave the stored value alone.
                    # A brand-new extension gets its kind from the guest-room number pattern.
                    extras, _invalid = parse_row_extras(row)
                    if 'kind' in extras:
                        item.kind = extras['kind']
                    elif existing is None:
                        item.kind = suggest_kind(extension_no)
                    if 'mac' in extras:
                        item.mac = extras['mac']
                    if 'phone_model' in extras:
                        item.phone_model = extras['phone_model']
                    item.is_active = str(row.get('status', 'فعال')).strip() not in ('غیرفعال', 'inactive', '0', 'False')
                    item.is_deleted = False
                    item.deleted_at = None
                    db.session.add(item)
                    saved += 1
                log_activity('imported', 'extension', f'ایمپورت فایل ({saved} رکورد)', details=os.path.basename(matches[0]))
                bump_data_version()
                db.session.commit()
            flash(f'{saved} داخلی با موفقیت وارد یا به‌روزرسانی شد.', 'success')
        except Exception:
            db.session.rollback()
            flash('فایل قابل خواندن نیست. ستون‌های استاندارد را بررسی کنید.', 'danger')
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/import/cancel', methods=['POST'])
    @login_required
    def import_cancel():
        token = session.pop('import_token', None)
        session.pop('import_original_name', None)
        session.pop('import_stats', None)
        if token:
            for path in glob.glob(os.path.join(IMPORT_TMP_DIR, f'{token}.*')):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return redirect(url_for('admin'))

    # ---- MAC import wizard: upload -> pick columns / preview -> apply -------------
    def find_mac_temp(token):
        if not token or not re.fullmatch(r'[0-9a-f]{32}', token):
            return None
        matches = glob.glob(os.path.join(IMPORT_TMP_DIR, f'mac_{token}.*'))
        return matches[0] if matches else None

    def clear_mac_temp():
        token = session.pop('mac_token', None)
        session.pop('mac_filename', None)
        path = find_mac_temp(token)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def load_mac_context(path):
        """Read the uploaded file and compute the dry-run for the column choices in the request."""
        def int_arg(name):
            value = request.values.get(name, '').strip()
            return int(value) if value.isdigit() else None

        try:
            sheetnames, rows = read_table(path, request.values.get('sheet') or None)
        except Exception:
            return None
        configured = request.values.get('configured') == '1'
        first_row = next((r for r in rows if any(cell_text(c) for c in r)), [])
        has_header = (request.values.get('has_header') == '1') if configured else not any(normalize_mac(c) for c in first_row)
        header, body = prepare_table(rows, has_header)
        auto_ext, auto_mac, auto_model = detect_mac_columns(header, body)
        if configured:
            ext_idx, mac_idx, model_idx = int_arg('ext_col'), int_arg('mac_col'), int_arg('model_col')
        else:
            ext_idx, mac_idx, model_idx = auto_ext, auto_mac, auto_model
        ncols = max([len(header)] + [len(cells) for _, cells in body] + [0])
        columns = []
        for i in range(ncols):
            title = cell_text(header[i]) if i < len(header) else ''
            columns.append((i, f'{col_letter(i)}: {title}' if title else f'ستون {col_letter(i)}'))
        overwrite = configured and request.values.get('overwrite') == '1'
        create_missing = configured and request.values.get('create_missing') == '1'
        plan = build_mac_plan(body, ext_idx, mac_idx, model_idx, overwrite, create_missing) if ext_idx is not None and mac_idx is not None else []
        summary = {}
        for item in plan:
            summary[item['action']] = summary.get(item['action'], 0) + 1
        return {
            'sheetnames': sheetnames, 'sheet': request.values.get('sheet') or (sheetnames[0] if sheetnames else ''),
            'has_header': has_header, 'columns': columns, 'ext_idx': ext_idx, 'mac_idx': mac_idx, 'model_idx': model_idx,
            'overwrite': overwrite, 'create_missing': create_missing, 'plan': plan, 'summary': summary,
            'shared_count': sum(1 for item in plan if item.get('shared')),
            'apply_count': sum(1 for item in plan if item['action'] in ('set', 'overwrite', 'create')),
        }

    @app.route(f'/{admin_prefix}/mac-import')
    @login_required
    def mac_import():
        _cleanup_stale_imports()
        path = find_mac_temp(session.get('mac_token'))
        if not path:
            return render_template('mac_import.html', stage='upload')
        ctx = load_mac_context(path)
        if ctx is None:
            clear_mac_temp()
            flash('فایل قابل خواندن نیست. یک فایل Excel (.xlsx) یا CSV معتبر انتخاب کنید.', 'danger')
            return render_template('mac_import.html', stage='upload')
        show = 'issues' if request.values.get('show') == 'issues' else 'all'
        shown = [item for item in ctx['plan'] if show == 'all' or item['action'] not in MAC_ACTIONS_APPLIED or item.get('shared')]
        return render_template('mac_import.html', stage='map', filename=session.get('mac_filename', ''), show=show,
                               shown=shown[:500], shown_total=len(shown), action_labels=MAC_ACTION_LABELS, **ctx)

    @app.route(f'/{admin_prefix}/mac-import/upload', methods=['POST'])
    @login_required
    def mac_import_upload():
        _cleanup_stale_imports()
        file = request.files.get('file')
        if not file or not file.filename:
            flash('فایلی انتخاب نشده است.', 'danger')
            return redirect(url_for('mac_import'))
        if not file.filename.lower().endswith(('.csv', '.xlsx')):
            flash('فقط فایل CSV یا XLSX پذیرفته می‌شود.', 'danger')
            return redirect(url_for('mac_import'))
        clear_mac_temp()
        token = uuid.uuid4().hex
        file.save(os.path.join(IMPORT_TMP_DIR, f'mac_{token}{os.path.splitext(file.filename)[1].lower()}'))
        session['mac_token'] = token
        session['mac_filename'] = file.filename
        return redirect(url_for('mac_import'))

    @app.route(f'/{admin_prefix}/mac-import/apply', methods=['POST'])
    @login_required
    def mac_import_apply():
        path = find_mac_temp(session.get('mac_token'))
        ctx = load_mac_context(path) if path else None
        if ctx is None:
            flash('فایل پیدا نشد؛ دوباره آپلود کنید.', 'danger')
            return redirect(url_for('mac_import'))
        if ctx['ext_idx'] is None or ctx['mac_idx'] is None:
            flash('ستون شماره داخلی و ستون MAC را انتخاب کنید.', 'danger')
            return redirect(url_for('mac_import'))
        try:
            counts = apply_mac_plan(ctx['plan'], overwrite=ctx['overwrite'])
            applied = counts['set'] + counts['overwrite'] + counts['create']
            log_activity('imported', 'extension', f'ایمپورت MAC ({applied} داخلی)', details=session.get('mac_filename', '')[:300])
            bump_data_version()
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception('MAC import failed')
            flash('ثبت MAC ناموفق بود. جزئیات در لاگ سرور ثبت شد.', 'danger')
            return redirect(url_for('mac_import'))
        clear_mac_temp()
        flash(f"MAC برای {applied} داخلی ثبت شد ({counts['set']} جدید، {counts['overwrite']} جایگزین، {counts['create']} داخلی تازه‌ساخته)."
              + (f" مدل تلفن برای {counts['model']} داخلی ثبت شد." if counts['model'] else ''), 'success')
        return redirect(url_for('admin'))

    @app.route(f'/{admin_prefix}/mac-import/cancel', methods=['POST'])
    @login_required
    def mac_import_cancel():
        clear_mac_temp()
        return redirect(url_for('admin'))

    # ---- PBX page: sync status, new extensions to review, offline phones ---------------
    @app.route(f'/{admin_prefix}/pbx')
    @login_required
    def pbx_status():
        live = Extension.query.filter(Extension.is_deleted == False)  # noqa: E712
        table = {kind: {'online': 0, 'offline': 0, 'unknown': 0, 'none': 0} for kind in EXTENSION_KINDS}
        for kind, status, total in db.session.query(Extension.kind, Extension.reg_status, func.count()).filter(Extension.is_deleted == False).group_by(Extension.kind, Extension.reg_status):  # noqa: E712
            if kind in table:
                table[kind][status if status in ('online', 'offline', 'unknown') else 'none'] += total
        has_synced = pbx_sync_state['last_success'] is not None
        pending = live.filter_by(kind='pending').order_by(Extension.extension).limit(200).all()
        offline = live.filter_by(reg_status='offline').order_by(Extension.kind, Extension.extension).limit(300).all()
        not_on_pbx = live.filter(Extension.kind == 'staff', Extension.status_updated_at == None).order_by(Extension.extension).limit(200).all() if has_synced else []  # noqa: E711
        mismatches = []
        for e in live.filter(Extension.mac != None, Extension.user_agent != None).all():  # noqa: E711
            ua_mac = parse_user_agent(e.user_agent)['mac']
            if ua_mac and ua_mac != e.mac:
                mismatches.append((e, ua_mac))
        return render_template('pbx.html', cfg=pbx_config(), state=pbx_sync_state, table=table, pending=pending, offline=offline,
                               not_on_pbx=not_on_pbx, has_synced=has_synced, mismatches=mismatches)

    @app.route(f'/{admin_prefix}/pbx/sync', methods=['POST'])
    @login_required
    def pbx_sync_now():
        if not pbx_config()['enabled']:
            flash('اتصال به PBX تنظیم نشده است.', 'warning')
            return redirect(url_for('pbx_status'))
        ok, result = run_pbx_sync(app)
        if ok:
            flash(f"همگام‌سازی انجام شد: {result['online']} آنلاین، {result['offline']} آفلاین، {result['unknown']} نامشخص"
                  + (f"، {result['created']} داخلی جدید" if result['created'] else '') + '.', 'success')
        else:
            flash(f'همگام‌سازی ناموفق بود: {result}', 'danger')
        return redirect(url_for('pbx_status'))

    @app.route(f'/{admin_prefix}/pbx/set-kind', methods=['POST'])
    @login_required
    def pbx_set_kind():
        entry = db.session.get(Extension, request.form.get('id', type=int) or 0)
        kind = request.form.get('kind', '')
        if entry is None or entry.is_deleted or kind not in ('staff', 'room'):
            flash('درخواست نامعتبر است.', 'danger')
            return redirect(url_for('pbx_status'))
        entry.kind = kind
        log_activity('updated', 'extension', f'{entry.extension} - {entry.title}', details=f'نوع: {KIND_LABELS[kind]}')
        bump_data_version()
        db.session.commit()
        flash(f'داخلی {entry.extension} به «{KIND_LABELS[kind]}» تبدیل شد.', 'success')
        return redirect(url_for('pbx_status'))

    # ---- JSON API: live phone state for the badges (polled by static/app.js) -------------
    def iso_utc(moment):
        return moment.isoformat() + 'Z' if moment else None

    def no_store(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/api/extensions/status')
    def api_extensions_status():
        """Phone state per extension. The public sees online / offline / unknown for the
        public directory (staff) only; a signed-in admin also gets rooms, latency, IP and sync info."""
        is_admin = current_user.is_authenticated
        query = Extension.query.filter(Extension.is_deleted == False, Extension.reg_status != None)  # noqa: E711,E712
        if not is_admin:
            query = query.filter(Extension.kind == 'staff')
        statuses = {}
        for entry in query.all():
            status = display_phone_status(entry)
            if not status:
                continue
            item = {'status': status, 'label': phone_label(entry), 'slow': phone_is_slow(entry)}
            if is_admin:
                item.update(kind=entry.kind, latency_ms=entry.rtt_ms, ip=entry.last_ip or None, last_sync=iso_utc(entry.status_updated_at))
            statuses[entry.extension] = item
        cfg = pbx_config()
        payload = {'ok': True, 'enabled': cfg['enabled'], 'interval': cfg['interval'], 'last_sync': iso_utc(pbx_sync_state['last_success']),
                   'generated_at': iso_utc(datetime.utcnow()), 'statuses': statuses}
        if is_admin:
            payload['sync'] = {'source': cfg['source'], 'running': pbx_sync_state['running'], 'last_attempt': iso_utc(pbx_sync_state['last_attempt']),
                               'last_error': pbx_sync_state['last_error']}
        return no_store(jsonify(payload))

    # ---- Outage report (admin) and the alert feed the browser polls ------------------------
    def outage_filters(args):
        return {'q': outage_report.normalize_digits(args.get('q', '')).strip()[:60],
                'from': outage_report.parse_day(args.get('from')), 'to': outage_report.parse_day(args.get('to')),
                'ongoing': args.get('ongoing') == '1'}

    def outage_query(filters):
        query = PhoneOutage.query.filter(PhoneOutage.confirmed.is_(True))
        if filters['q']:
            pattern = f"%{filters['q']}%"
            query = query.filter(or_(PhoneOutage.extension.ilike(pattern), PhoneOutage.title.ilike(pattern), PhoneOutage.last_ip.ilike(pattern)))
        start, end = outage_report.day_bounds_utc(filters['from'], filters['to'], LOCAL_TZ)
        if start is not None:
            query = query.filter(PhoneOutage.started_at >= start)
        if end is not None:
            query = query.filter(PhoneOutage.started_at < end)
        if filters['ongoing']:
            query = query.filter(PhoneOutage.ended_at.is_(None))
        return query

    def outage_status(outage):
        if outage.ended_at is None:
            return 'ongoing'
        return 'stopped' if outage.end_reason == 'stopped' else 'ended'

    @app.route(f'/{admin_prefix}/outages')
    @login_required
    def outages_report():
        filters = outage_filters(request.args)
        now = datetime.utcnow()
        confirmed = PhoneOutage.query.filter(PhoneOutage.confirmed.is_(True))
        recent = confirmed.filter(PhoneOutage.started_at >= now - timedelta(days=30)).order_by(PhoneOutage.started_at).all()
        stats = {
            'offline_now': confirmed.filter(PhoneOutage.ended_at.is_(None)).count(),
            'today': confirmed.filter(PhoneOutage.started_at >= outage_report.local_midnight_utc(now, LOCAL_TZ)).count(),
            'worst': outage_report.worst_offenders(((o.extension, o.title, o.duration_seconds) for o in recent), 5),
        }
        query = outage_query(filters)
        total = query.count()
        rows = query.order_by(PhoneOutage.started_at.desc(), PhoneOutage.id.desc()).limit(OUTAGE_PAGE_LIMIT).all()
        cfg = pbx_config()
        return render_template('outages.html', rows=rows, total=total, limit=OUTAGE_PAGE_LIMIT, stats=stats, filters=filters,
                               outage_status=outage_status, iso_utc=iso_utc, format_duration=outage_monitor.format_duration,
                               enabled=cfg['outage_enabled'] and cfg['enabled'], keep_days=cfg['outage_keep_days'])

    @app.route(f'/{admin_prefix}/outages/export.csv')
    @login_required
    def outages_export():
        filters = outage_filters(request.args)
        rows = outage_query(filters).order_by(PhoneOutage.started_at.desc(), PhoneOutage.id.desc()).limit(OUTAGE_CSV_LIMIT).all()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['extension', 'title', 'ip', 'started_at', 'ended_at', 'duration_seconds', 'duration', 'status'])
        stamp = '%Y-%m-%d %H:%M:%S'
        for o in rows:
            seconds = o.duration_seconds
            writer.writerow([o.extension, outage_report.csv_safe(o.title), o.last_ip or '', format_local_datetime(o.started_at, stamp),
                             format_local_datetime(o.ended_at, stamp) if o.ended_at else '', seconds,
                             outage_monitor.format_duration(seconds), outage_status(o)])
        name = 'phone_outages_' + format_local_datetime(datetime.utcnow(), '%Y%m%d_%H%M') + '.csv'
        return send_file(io.BytesIO(buffer.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=name)

    @app.route('/api/outages/alerts')
    def api_outage_alerts():
        """Admin only. New outage / recovery alerts since the event id `after` (the browser keeps the last one it saw).
        Without a usable `after` (first call, or the database was reset) only `latest_id` comes back, so a
        freshly opened browser starts from 'now' instead of replaying history."""
        if not current_user.is_authenticated:
            return no_store(jsonify(ok=False, error='unauthorized')), 401
        cfg = pbx_config()
        latest = db.session.query(func.max(OutageEvent.id)).scalar() or 0
        payload = {'ok': True, 'enabled': cfg['alerts_enabled'], 'latest_id': latest, 'alerts': []}
        after = request.args.get('after', type=int)
        if not cfg['alerts_enabled'] or after is None or after < 0 or after > latest:
            return no_store(jsonify(payload))
        fresh_from = datetime.utcnow() - timedelta(minutes=OUTAGE_ALERT_MAX_AGE_MINUTES)
        events = [{'id': e.id, 'kind': e.kind, 'batch': iso_utc(e.batch), 'extension': e.extension, 'title': e.title, 'detail': e.detail}
                  for e in OutageEvent.query.filter(OutageEvent.id > after, OutageEvent.created_at >= fresh_from).order_by(OutageEvent.id).limit(500).all()]
        payload['alerts'] = outage_alerts.plan_alerts(events, cfg['alert_summary_threshold'], cfg['alert_on_recovery'], url_for('outages_report'))
        return no_store(jsonify(payload))

    @app.route('/api/pbx/sync-now', methods=['POST'])
    def api_pbx_sync_now():
        """Admin only: run one sync cycle right now and return its summary (CSRF token required in X-CSRFToken)."""
        if not current_user.is_authenticated:
            return no_store(jsonify(ok=False, error='unauthorized')), 401
        if not getattr(current_user, 'is_admin', False):
            return no_store(jsonify(ok=False, error='forbidden')), 403
        if not pbx_config()['enabled']:
            return no_store(jsonify(ok=False, error='اتصال به PBX تنظیم نشده است.')), 409
        ok, result = run_pbx_sync(app)
        if ok:
            return no_store(jsonify(ok=True, summary=result, last_sync=iso_utc(pbx_sync_state['last_success'])))
        return no_store(jsonify(ok=False, error=str(result))), (409 if pbx_sync_lock.locked() else 502)

    # Background polling; PBX_SYNC_AUTOSTART=0 disables the thread (the page's button still works).
    in_reloader_parent = os.getenv('FLASK_DEBUG', '0') == '1' and os.getenv('WERKZEUG_RUN_MAIN') != 'true'
    if os.getenv('PBX_SYNC_AUTOSTART', '1') == '1' and not in_reloader_parent:
        start_pbx_sync_thread(app)

    return app


def export_headers(include_contact=False, include_network=False):
    columns = ['extension', 'title', 'person_name', 'department', 'location']
    if include_contact:
        columns += ['email', 'mobile']
    columns += ['notes', 'status', 'present']
    if include_network:
        columns += ['kind', 'mac', 'phone_model']
    return columns


def export_rows(entries, include_contact=False, include_network=False):
    rows = []
    for e in entries:
        row = {'extension': e.extension, 'title': e.title, 'person_name': e.person_name or '', 'department': e.department.name if e.department else '', 'location': e.location or ''}
        if include_contact:
            row['email'] = e.email or ''
            row['mobile'] = e.mobile or ''
        row['notes'] = e.notes or ''
        row['status'] = 'فعال' if e.is_active else 'غیرفعال'
        row['present'] = 'حاضر' if e.is_present else 'خارج از شیفت'
        if include_network:
            row['kind'] = e.kind
            row['mac'] = e.mac or ''
            row['phone_model'] = e.phone_model or ''
        rows.append(row)
    return rows


def build_tabular_export(fmt, entries, include_contact=False, include_network=False):
    """Build a CSV/XLSX send_file response for the given entries, or None for an unknown format.
    E-mail / mobile columns need include_contact; kind / MAC / phone model need include_network (admin only)."""
    rows = export_rows(entries, include_contact, include_network)
    if fmt == 'csv':
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=export_headers(include_contact, include_network))
        writer.writeheader(); writer.writerows(rows)
        return send_file(io.BytesIO(buffer.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name='hotel_extensions.csv')
    if fmt == 'xlsx':
        wb = Workbook(); ws = wb.active; ws.title = 'Internals'
        ws.append(export_headers(include_contact, include_network))
        for row in rows: ws.append(list(row.values()))
        ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = min(max(len(str(c.value or '')) for c in column) + 2, 35)
        output = io.BytesIO(); wb.save(output); output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='hotel_extensions.xlsx')
    return None


def _cleanup_stale_imports(max_age_seconds=3600):
    """Remove leftover temp import files from abandoned preview sessions."""
    try:
        now = datetime.utcnow().timestamp()
        for path in glob.glob(os.path.join(IMPORT_TMP_DIR, '*')):
            try:
                if now - os.path.getmtime(path) > max_age_seconds:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def get_or_create_department(name):
    item = Department.query.filter_by(name=name).first()
    if not item: item = Department(name=name); db.session.add(item)
    return item
def read_import_file(file):
    if file.filename.lower().endswith('.csv'):
        return list(csv.DictReader(io.StringIO(file.read().decode('utf-8-sig'))))
    workbook = load_workbook(file, read_only=True, data_only=True); sheet = workbook.active
    headers = [str(c.value or '').strip() for c in next(sheet.iter_rows(max_row=1))]
    return [dict(zip(headers, [c.value if c.value is not None else '' for c in row])) for row in sheet.iter_rows(min_row=2)]


def pdf_text(value):
    """Shape Persian text for correct right-to-left rendering in ReportLab.
    The result is XML-escaped because Paragraph parses its text as markup
    (otherwise a name such as "F&B" is printed as "F&B;")."""
    return xml_escape(get_display(reshape(str(value or '—'))))


def build_extensions_pdf(output, extensions, font_path):
    """Compact two-column landscape report: black title bar, then a
    continuous table per column where each department's name is a single
    cell merged (spanned) vertically alongside all of its own rows, and
    the next department starts immediately after — no header band, no
    gap between blocks, to save paper when printed.

    `extensions` is a flat, already-filtered/sorted list of Extension
    rows (e.g. matching the current homepage search) — grouped by
    department here so exports always match what's on screen."""
    font_name = 'TahomaPDF'
    try:
        pdfmetrics.getFont(font_name)
    except KeyError:
        pdfmetrics.registerFont(TTFont(font_name, font_path))

    page_w, page_h = landscape(A4)
    margin = 1 * cm
    gutter = 0.7 * cm
    col_width = (page_w - 2 * margin - gutter) / 2

    document = BaseDocTemplate(
        output, pagesize=landscape(A4),
        rightMargin=margin, leftMargin=margin, topMargin=margin, bottomMargin=margin,
    )
    frame_right = Frame(page_w - margin - col_width, margin, col_width, page_h - 2 * margin, id='right', showBoundary=0)
    frame_left = Frame(margin, margin, col_width, page_h - 2 * margin, id='left', showBoundary=0)
    document.addPageTemplates([PageTemplate(id='two-col', frames=[frame_right, frame_left])])

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('PersianTitle', parent=styles['Title'], fontName=font_name, fontSize=13, leading=18, alignment=TA_CENTER, textColor=colors.white)
    empty_style = ParagraphStyle('PersianEmpty', parent=styles['Normal'], fontName=font_name, fontSize=10, alignment=TA_RIGHT, textColor=colors.HexColor('#6b7686'))
    dept_style = ParagraphStyle('DeptCell', parent=styles['Normal'], fontName=font_name, fontSize=9.3, leading=12, alignment=TA_CENTER, textColor=colors.HexColor('#12263f'))
    cell_style = ParagraphStyle('PersianCell', parent=styles['Normal'], fontName=font_name, fontSize=8.3, leading=12, alignment=TA_RIGHT)
    cell_style_num = ParagraphStyle('PersianCellNum', parent=cell_style, alignment=TA_CENTER)

    story = []
    title_table = Table([[Paragraph(pdf_text('لیست داخلی‌های هتل آراز'), title_style)]], colWidths=[col_width])
    title_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.append(title_table)
    story.append(Spacer(1, 0.3 * cm))

    grouped = {}
    for entry in extensions:
        dept_name = entry.department.name if entry.department else 'بدون بخش'
        grouped.setdefault(dept_name, []).append(entry)

    if not grouped:
        story.append(Paragraph(pdf_text('هیچ داخلی‌ای برای این خروجی پیدا نشد.'), empty_style))

    dept_col_w = col_width * 0.30
    title_col_w = col_width * 0.46
    ext_col_w = col_width * 0.24

    for dept_name in sorted(grouped.keys()):
        entries = sorted(grouped[dept_name], key=lambda item: item.extension)
        n = len(entries)
        rows = []
        for i, entry in enumerate(entries):
            dept_cell = Paragraph(pdf_text(dept_name), dept_style) if i == 0 else ''
            rows.append([dept_cell, Paragraph(pdf_text(entry.title), cell_style), Paragraph(pdf_text(entry.extension), cell_style_num)])
        block = Table(rows, colWidths=[dept_col_w, title_col_w, ext_col_w])
        style_cmds = [
            ('SPAN', (0, 0), (0, n - 1)),
            ('BACKGROUND', (0, 0), (0, n - 1), colors.HexColor('#eef1f6')),
            ('VALIGN', (0, 0), (0, n - 1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#c7cdd8')),
            ('VALIGN', (1, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.2),
        ]
        for row_index in range(n):
            bg = colors.white if row_index % 2 == 0 else colors.HexColor('#f6f8fb')
            style_cmds.append(('BACKGROUND', (1, row_index), (2, row_index), bg))
        block.setStyle(TableStyle(style_cmds))
        story.append(KeepTogether([block]))

    document.build(story)
def flag_default_passwords(app):
    """Force a password change for every account that still uses a
    password shipped with the project (e.g. admin / admin123)."""
    flagged = []
    for user in User.query.filter_by(must_change_password=False).all():
        if any(user.check_password(p) for p in DEFAULT_PASSWORDS):
            user.must_change_password = True
            flagged.append(user.username)
    if flagged:
        db.session.commit()
        app.logger.warning('Default password still in use; password change forced for: %s', ', '.join(flagged))


def seed_data():
    if not User.query.first():
        user = User(username='admin', display_name='مدیر سیستم', must_change_password=True)
        user.set_password('admin123')
        db.session.add(user)
    if not Department.query.first():
        for name in ('پذیرش', 'خانه‌داری', 'رستوران', 'مدیریت', 'فنی'): db.session.add(Department(name=name))
    db.session.commit()


if __name__ == '__main__':
    # This built-in server is for local development only. In production,
    # run behind a real WSGI server instead — see DEPLOYMENT.md.
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    create_app().run(debug=debug_mode, host='127.0.0.1', port=5000)