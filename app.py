import csv
import glob
import io
import os
import re
import uuid
from datetime import datetime



from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
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
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login'


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
    statements = []
    if 'email' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN email VARCHAR(150)')
    if 'mobile' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN mobile VARCHAR(30)')
    if 'is_present' not in existing_ext_cols:
        statements.append('ALTER TABLE extension ADD COLUMN is_present BOOLEAN DEFAULT 0 NOT NULL')
    if 'working_hours' not in existing_dept_cols:
        statements.append('ALTER TABLE department ADD COLUMN working_hours VARCHAR(120)')
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
            'total_extensions': Extension.query.count(),
            'total_departments': Department.query.count(),
            'active_count': Extension.query.filter_by(is_active=True).count(),
            'inactive_count': Extension.query.filter_by(is_active=False).count(),
            'last_updated': db.session.query(func.max(Extension.updated_at)).scalar(),
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
        SECRET_KEY=os.getenv('SECRET_KEY', 'please-change-this-secret-key'),
        SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(BASE_DIR, 'hotel_extensions.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        PDF_FONT_PATH=os.getenv('PDF_FONT_PATH', r'C:\Windows\Fonts\Tahoma.ttf'),
        SEND_FILE_MAX_AGE_DEFAULT=1800,  # cache static assets (CSS/JS) in the browser for 30 min
    )
    db.init_app(app)
    login_manager.init_app(app)
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

    @app.route('/api/version')
    def api_version():
        return {'version': _data_version}

    @app.route('/')
    def index():
        query = request.args.get('q', '').strip()
        department_id = request.args.get('department', type=int)
        status = request.args.get('status', '')
        per_page_raw = request.args.get('per_page', '20').strip().lower()
        page = request.args.get('page', 1, type=int)
        if page < 1:
            page = 1

        # advanced search fields
        ext_q = request.args.get('ext', '').strip()
        person_q = request.args.get('person', '').strip()
        title_q = request.args.get('title_q', '').strip()
        location_q = request.args.get('location_q', '').strip()

        # sorting: value comes in as "field:dir"
        sort_map = {
            'extension': Extension.extension,
            'title': Extension.title,
            'department': Department.name,
            'location': Extension.location,
            'created': Extension.created_at,
            'updated': Extension.updated_at,
        }
        sort_value = request.args.get('sort', 'extension:asc')
        sort_field, _, sort_dir = sort_value.partition(':')
        if sort_field not in sort_map:
            sort_field = 'extension'
        if sort_dir not in ('asc', 'desc'):
            sort_dir = 'asc'
        sort_value = f'{sort_field}:{sort_dir}'

        entries = Extension.query
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

        total_count = entries.count()

        sort_column = sort_map[sort_field]
        entries = entries.order_by(sort_column.desc() if sort_dir == 'desc' else sort_column.asc())

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
            query=query,
            selected_department=department_id,
            status=status,
            ext_q=ext_q,
            person_q=person_q,
            title_q=title_q,
            location_q=location_q,
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
        entries_query = Extension.query
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
            user = User.query.filter_by(username=request.form.get('username', '').strip()).first()
            if user and user.check_password(request.form.get('password', '')):
                login_user(user)
                return redirect(url_for('admin'))
            flash('نام کاربری یا رمز عبور صحیح نیست.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('index'))

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
        if duplicate.first():
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
        log_activity('deleted', 'extension', f'{entry.extension} — {entry.title}')
        db.session.delete(entry)
        bump_data_version()
        db.session.commit()
        flash('داخلی حذف شد.', 'success')
        return redirect(url_for('admin'))

    @app.route('/admin/extensions/delete-all', methods=['POST'])
    @login_required
    def delete_all_extensions():
        if request.form.get('confirmation', '').strip() != 'پاک کن':
            flash('برای پاک‌سازی، عبارت «پاک کن» را دقیق وارد کنید.', 'danger')
            return redirect(url_for('admin'))
        count = Extension.query.delete()
        log_activity('deleted', 'extension', f'پاک‌سازی گروهی ({count} داخلی)')
        bump_data_version()
        db.session.commit()
        flash(f'{count} داخلی با موفقیت پاک شد.', 'success')
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

    @app.route('/admin/export/<fmt>')
    @login_required
    def export_data(fmt):
        response = build_tabular_export(fmt)
        if response is None:
            return 'فرمت نامعتبر است.', 404
        return response

    @app.route('/export/<fmt>')
    def public_export(fmt):
        if fmt not in ('csv', 'xlsx'):
            return 'فرمت نامعتبر است.', 404
        response = build_tabular_export(fmt)
        if response is None:
            return 'فرمت نامعتبر است.', 404
        return response

    @app.route('/export/pdf')
    def public_export_pdf():
        departments = Department.query.order_by(Department.name).all()
        if not departments:
            flash('هنوز داخلی‌ای برای خروجی گرفتن ثبت نشده است.', 'danger')
            return redirect(url_for('index'))
        font_path = app.config['PDF_FONT_PATH']
        if not os.path.isfile(font_path):
            flash('فونت فارسی برای ساخت PDF پیدا نشد. مسیر PDF_FONT_PATH را تنظیم کنید.', 'danger')
            return redirect(url_for('index'))
        output = io.BytesIO()
        build_extensions_pdf(output, departments, font_path)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='hotel_extensions.pdf')

    @app.route('/admin/export/pdf')
    @login_required
    def export_pdf():
        department_id = request.args.get('department_id', type=int)
        departments = Department.query.order_by(Department.name)
        if department_id:
            departments = departments.filter_by(id=department_id)
        departments = departments.all()
        if not departments:
            flash('بخش انتخاب‌شده پیدا نشد.', 'danger')
            return redirect(url_for('admin'))
        font_path = app.config['PDF_FONT_PATH']
        if not os.path.isfile(font_path):
            flash('فونت فارسی برای ساخت PDF پیدا نشد. مسیر PDF_FONT_PATH را تنظیم کنید.', 'danger')
            return redirect(url_for('admin'))
        output = io.BytesIO()
        build_extensions_pdf(output, departments, font_path)
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
def export_rows():
    return [{'extension': e.extension, 'title': e.title, 'person_name': e.person_name or '', 'department': e.department.name if e.department else '', 'location': e.location or '', 'notes': e.notes or '', 'status': 'فعال' if e.is_active else 'غیرفعال'} for e in Extension.query.order_by(Extension.extension).all()]


def build_tabular_export(fmt):
    """Build a CSV/XLSX send_file response for the full extension list, or None for an unknown format."""
    rows = export_rows()
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


def build_extensions_pdf(output, departments, font_path):
    """Compact two-column landscape report: black title bar + one small
    table per department (department name as a header band, then
    title/extension rows), flowing departments through two columns."""
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

    story = []
    title_table = Table([[Paragraph(pdf_text('لیست داخلی‌های هتل آراز'), title_style)]], colWidths=[col_width])
    title_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.append(title_table)
    story.append(Spacer(1, 0.4 * cm))

    included = [d for d in departments if d.extension_entries]
    if not included:
        story.append(Paragraph(pdf_text('هیچ داخلی‌ای برای بخش انتخاب‌شده ثبت نشده است.'), empty_style))

    dept_header_style = ParagraphStyle('DeptHeader', parent=styles['Normal'], fontName=font_name, fontSize=10, leading=14, alignment=TA_CENTER, textColor=colors.white)
    cell_style = ParagraphStyle('PersianCell', parent=styles['Normal'], fontName=font_name, fontSize=8.3, leading=12, alignment=TA_RIGHT)
    cell_style_num = ParagraphStyle('PersianCellNum', parent=cell_style, alignment=TA_CENTER)

    for department in included:
        entries = sorted(department.extension_entries, key=lambda item: item.extension)
        rows = [[Paragraph(pdf_text(department.name), dept_header_style), '']]
        for entry in entries:
            rows.append([
                Paragraph(pdf_text(entry.title), cell_style),
                Paragraph(pdf_text(entry.extension), cell_style_num),
            ])
        block = Table(rows, colWidths=[col_width * 0.68, col_width * 0.32])
        row_styles = [
            ('SPAN', (0, 0), (-1, 0)),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d2340')),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#dcdfe6')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
            ('TOPPADDING', (0, 0), (-1, 0), 5), ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
        ]
        for row_index in range(1, len(rows)):
            if row_index % 2 == 0:
                row_styles.append(('BACKGROUND', (0, row_index), (-1, row_index), colors.HexColor('#f4f6fa')))
            else:
                row_styles.append(('BACKGROUND', (0, row_index), (-1, row_index), colors.white))
        block.setStyle(TableStyle(row_styles))
        story.append(KeepTogether([block, Spacer(1, 0.3 * cm)]))

    document.build(story)
def seed_data():
    if not User.query.first():
        user = User(username='admin', display_name='مدیر سیستم'); user.set_password('admin123'); db.session.add(user)
    if not Department.query.first():
        for name in ('پذیرش', 'خانه‌داری', 'رستوران', 'مدیریت', 'فنی'): db.session.add(Department(name=name))
    db.session.commit()


if __name__ == '__main__':
    create_app().run(debug=True, host='192.168.142.135', port=5000)