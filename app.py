import csv
import glob
import io
import os
import re
import secrets
import uuid
from datetime import datetime



from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
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
from werkzeug.datastructures import FileStorage
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
IMPORT_TMP_DIR = os.path.join(BASE_DIR, 'tmp_imports')
SECRET_KEY_FILE = os.path.join(BASE_DIR, '.secret_key')
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login'


def get_or_create_secret_key():
    """Use SECRET_KEY from the environment if set; otherwise generate a
    random key once and persist it to a local file so it survives
    restarts, instead of falling back to a hardcoded shared value."""
    env_key = os.getenv('SECRET_KEY')
    if env_key:
        return env_key
    if os.path.isfile(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r', encoding='utf-8') as f:
            key = f.read().strip()
            if key:
                return key
    key = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_FILE, 'w', encoding='utf-8') as f:
            f.write(key)
    except OSError:
        pass
    return key


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


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=get_or_create_secret_key(),
        SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(BASE_DIR, 'hotel_extensions.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        PDF_FONT_PATH=os.getenv('PDF_FONT_PATH', r'C:\Windows\Fonts\Tahoma.ttf'),
        SEND_FILE_MAX_AGE_DEFAULT=1800,  # cache static assets (CSS/JS) in the browser for 30 min
    )
    db.init_app(app)
    login_manager.init_app(app)
    CSRFProtect(app)
    app.jinja_env.globals['dept_icon'] = department_icon
    app.jinja_env.globals['data_version'] = lambda: _data_version
    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    with app.app_context():
        db.create_all()
        _ensure_columns(db.engine)
        seed_data()

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

        entries = Extension.query.filter_by(is_deleted=False)
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

    @app.route('/admin')
    @login_required
    def admin():
        q = request.args.get('q', '').strip()
        entries_query = Extension.query.filter_by(is_deleted=False)
        if q:
            pattern = f'%{q}%'
            entries_query = entries_query.filter(or_(Extension.extension.ilike(pattern), Extension.title.ilike(pattern), Extension.person_name.ilike(pattern), Extension.location.ilike(pattern)))
        entries = entries_query.order_by(Extension.extension).all()
        return render_template(
            'admin.html',
            entries=entries,
            departments=Department.query.order_by(Department.name).all(),
            q=q,
            import_stats=session.get('import_stats'),
            import_filename=session.get('import_original_name'),
            stats=get_cached_stats(),
        )

    @app.route('/admin/activity-log')
    @login_required
    def activity_log():
        logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(300).all()
        return render_template('activity_log.html', logs=logs)

    @app.route('/login', methods=['GET', 'POST'])
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

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('index'))

    @app.route('/admin/change-password', methods=['GET', 'POST'])
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

    @app.route('/admin/users')
    @login_required
    def admin_users():
        users = User.query.order_by(User.username).all()
        return render_template('admin_users.html', users=users)

    @app.route('/admin/users', methods=['POST'])
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

    @app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
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

    @app.route('/admin/extensions/new', methods=['GET', 'POST'])
    @login_required
    def create_extension():
        if request.method == 'POST':
            return save_extension()
        return render_template('extension_form.html', entry=None, departments=Department.query.order_by(Department.name).all())

    @app.route('/admin/extensions/<int:entry_id>/edit', methods=['GET', 'POST'])
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
        db.session.add(entry)
        log_activity('created' if is_new else 'updated', 'extension', f'{extension_no} — {title}')
        bump_data_version()
        db.session.commit()
        flash('داخلی با موفقیت ذخیره شد.', 'success')
        return redirect(url_for('admin'))

    @app.route('/admin/extensions/<int:entry_id>/delete', methods=['POST'])
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

    @app.route('/admin/extensions/delete-all', methods=['POST'])
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

    @app.route('/admin/extensions/<int:entry_id>/restore', methods=['POST'])
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

    @app.route('/admin/extensions/<int:entry_id>/purge', methods=['POST'])
    @login_required
    def purge_extension(entry_id):
        entry = db.get_or_404(Extension, entry_id)
        log_activity('deleted', 'extension', f'{entry.extension} — {entry.title}', details='حذف همیشگی')
        db.session.delete(entry)
        bump_data_version()
        db.session.commit()
        flash('داخلی برای همیشه حذف شد.', 'success')
        return redirect(url_for('trash'))

    @app.route('/admin/trash')
    @login_required
    def trash():
        deleted_entries = Extension.query.filter_by(is_deleted=True).order_by(Extension.deleted_at.desc()).all()
        return render_template('trash.html', entries=deleted_entries)

    @app.route('/admin/extensions/bulk-activate', methods=['POST'])
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

    @app.route('/admin/extensions/bulk-deactivate', methods=['POST'])
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

    @app.route('/admin/extensions/bulk-delete', methods=['POST'])
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

    @app.route('/admin/departments', methods=['POST'])
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

    @app.route('/admin/departments/<int:department_id>/edit', methods=['POST'])
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

    @app.route('/admin/departments/<int:department_id>/delete', methods=['POST'])
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

    @app.route('/admin/backup')
    @login_required
    def download_backup():
        db_path = os.path.join(BASE_DIR, 'hotel_extensions.db')
        if not os.path.isfile(db_path):
            flash('فایل دیتابیس پیدا نشد.', 'danger')
            return redirect(url_for('admin'))
        stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        log_activity('exported', 'backup', 'دانلود نسخه پشتیبان کامل دیتابیس')
        db.session.commit()
        return send_file(db_path, mimetype='application/octet-stream', as_attachment=True, download_name=f'hotel_extensions_backup_{stamp}.db')

    @app.route('/admin/export/<fmt>')
    @login_required
    def export_data(fmt):
        entries = Extension.query.filter_by(is_deleted=False).order_by(Extension.extension).all()
        response = build_tabular_export(fmt, entries)
        if response is None:
            return 'فرمت نامعتبر است.', 404
        return response

    @app.route('/export/<fmt>')
    def public_export(fmt):
        if fmt not in ('csv', 'xlsx'):
            return 'فرمت نامعتبر است.', 404
        entries, _, _ = build_filtered_query(request.args)
        response = build_tabular_export(fmt, entries.all())
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
        font_path = app.config['PDF_FONT_PATH']
        if not os.path.isfile(font_path):
            flash('فونت فارسی برای ساخت PDF پیدا نشد. مسیر PDF_FONT_PATH را تنظیم کنید.', 'danger')
            return redirect(url_for('index'))
        output = io.BytesIO()
        build_extensions_pdf(output, entries, font_path)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='hotel_extensions.pdf')

    @app.route('/admin/export/pdf')
    @login_required
    def export_pdf():
        department_id = request.args.get('department_id', type=int)
        entries_query = Extension.query.filter_by(is_deleted=False)
        if department_id:
            entries_query = entries_query.filter_by(department_id=department_id)
        entries = entries_query.order_by(Extension.extension).all()
        if not entries:
            flash('داخلی‌ای برای بخش انتخاب‌شده پیدا نشد.', 'danger')
            return redirect(url_for('admin'))
        font_path = app.config['PDF_FONT_PATH']
        if not os.path.isfile(font_path):
            flash('فونت فارسی برای ساخت PDF پیدا نشد. مسیر PDF_FONT_PATH را تنظیم کنید.', 'danger')
            return redirect(url_for('admin'))
        output = io.BytesIO()
        build_extensions_pdf(output, entries, font_path)
        output.seek(0)
        name = 'hotel_extensions_all_departments.pdf' if not department_id else f'hotel_extensions_department_{department_id}.pdf'
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name=name)

    @app.route('/admin/import/preview', methods=['POST'])
    @login_required
    def import_preview():
        _cleanup_stale_imports()
        file = request.files.get('file')
        if not file or not file.filename:
            flash('فایلی انتخاب نشده است.', 'danger')
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
            if Extension.query.filter_by(extension=extension_no).first():
                existing_updates += 1

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
        }
        return redirect(url_for('admin'))

    @app.route('/admin/import/confirm', methods=['POST'])
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
                    item = Extension.query.filter_by(extension=extension_no).first() or Extension(extension=extension_no)
                    item.title = title
                    item.person_name = str(row.get('person_name', '')).strip() or None
                    item.department = department
                    item.location = str(row.get('location', '')).strip() or None
                    item.notes = str(row.get('notes', '')).strip() or None
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

    @app.route('/admin/import/cancel', methods=['POST'])
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

    return app


def export_headers(): return ['extension', 'title', 'person_name', 'department', 'location', 'notes', 'status']
def export_rows(entries):
    return [{'extension': e.extension, 'title': e.title, 'person_name': e.person_name or '', 'department': e.department.name if e.department else '', 'location': e.location or '', 'notes': e.notes or '', 'status': 'فعال' if e.is_active else 'غیرفعال'} for e in entries]


def build_tabular_export(fmt, entries):
    """Build a CSV/XLSX send_file response for the given entries, or None for an unknown format."""
    rows = export_rows(entries)
    if fmt == 'csv':
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()) if rows else export_headers())
        writer.writeheader(); writer.writerows(rows)
        return send_file(io.BytesIO(buffer.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name='hotel_extensions.csv')
    if fmt == 'xlsx':
        wb = Workbook(); ws = wb.active; ws.title = 'Internals'
        ws.append(export_headers())
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
    """Shape Persian text for correct right-to-left rendering in ReportLab."""
    return get_display(reshape(str(value or '—')))


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
def seed_data():
    if not User.query.first():
        user = User(username='admin', display_name='مدیر سیستم', must_change_password=True)
        user.set_password('admin123')
        db.session.add(user)
    if not Department.query.first():
        for name in ('پذیرش', 'خانه‌داری', 'رستوران', 'مدیریت', 'فنی'): db.session.add(Department(name=name))
    db.session.commit()


if __name__ == '__main__':
    create_app().run(debug=True, host='0.0.0.0', port=5000)
