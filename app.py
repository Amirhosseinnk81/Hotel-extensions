import csv
import io
import os
from datetime import datetime



from flask import Flask, flash, redirect, render_template, request, send_file, url_for
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
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login'


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
    extension_entries = db.relationship('Extension', backref='department', lazy=True)


class Extension(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    extension = db.Column(db.String(20), unique=True, nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False, index=True)
    person_name = db.Column(db.String(150), index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True, index=True)
    location = db.Column(db.String(150))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY', 'please-change-this-secret-key'),
        SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(BASE_DIR, 'hotel_extensions.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        PDF_FONT_PATH=os.getenv('PDF_FONT_PATH', r'C:\Windows\Fonts\Tahoma.ttf'),
    )
    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    with app.app_context():
        db.create_all()
        seed_data()

    @app.route('/')
    def index():
        query = request.args.get('q', '').strip()
        department_id = request.args.get('department', type=int)
        status = request.args.get('status', '')
        entries = Extension.query
        if query:
            pattern = f'%{query}%'
            entries = entries.filter(or_(Extension.extension.ilike(pattern), Extension.title.ilike(pattern), Extension.person_name.ilike(pattern), Extension.location.ilike(pattern)))
        if department_id:
            entries = entries.filter_by(department_id=department_id)
        if status == 'active':
            entries = entries.filter_by(is_active=True)
        elif status == 'inactive':
            entries = entries.filter_by(is_active=False)
        entries = entries.order_by(Extension.extension).all()
        return render_template('index.html', entries=entries, departments=Department.query.order_by(Department.name).all(), query=query, selected_department=department_id, status=status)

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

    @app.route('/admin')
    @login_required
    def admin():
        return render_template('admin.html', entries=Extension.query.order_by(Extension.extension).all(), departments=Department.query.order_by(Department.name).all())

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
        entry = entry or Extension()
        entry.extension = extension_no
        entry.title = title
        entry.person_name = request.form.get('person_name', '').strip() or None
        entry.department_id = request.form.get('department_id', type=int) or None
        entry.location = request.form.get('location', '').strip() or None
        entry.notes = request.form.get('notes', '').strip() or None
        entry.is_active = request.form.get('is_active') == 'on'
        db.session.add(entry)
        db.session.commit()
        flash('داخلی با موفقیت ذخیره شد.', 'success')
        return redirect(url_for('admin'))

    @app.route('/admin/extensions/<int:entry_id>/delete', methods=['POST'])
    @login_required
    def delete_extension(entry_id):
        db.session.delete(db.get_or_404(Extension, entry_id))
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
        db.session.commit()
        flash(f'{count} داخلی با موفقیت پاک شد.', 'success')
        return redirect(url_for('admin'))

    @app.route('/admin/departments', methods=['POST'])
    @login_required
    def create_department():
        name = request.form.get('name', '').strip()
        if name and not Department.query.filter_by(name=name).first():
            db.session.add(Department(name=name))
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
        if not name:
            flash('نام بخش نمی‌تواند خالی باشد.', 'danger')
        elif Department.query.filter(Department.name == name, Department.id != department.id).first():
            flash('بخشی با این نام از قبل وجود دارد.', 'danger')
        else:
            department.name = name
            db.session.commit()
            flash('نام بخش ویرایش شد.', 'success')
        return redirect(url_for('admin'))

    @app.route('/admin/departments/<int:department_id>/delete', methods=['POST'])
    @login_required
    def delete_department(department_id):
        department = db.get_or_404(Department, department_id)
        Extension.query.filter_by(department_id=department.id).update({Extension.department_id: None}, synchronize_session=False)
        db.session.delete(department)
        db.session.commit()
        flash('بخش حذف شد؛ داخلی‌های آن بدون بخش باقی مانده‌اند.', 'success')
        return redirect(url_for('admin'))

    @app.route('/admin/export/<fmt>')
    @login_required
    def export_data(fmt):
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
        return 'فرمت نامعتبر است.', 404

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

    @app.route('/admin/import', methods=['POST'])
    @login_required
    def import_data():
        file = request.files.get('file')
        if not file or not file.filename:
            flash('فایلی انتخاب نشده است.', 'danger'); return redirect(url_for('admin'))
        try:
            rows = read_import_file(file)
            saved = 0
            for row in rows:
                extension_no = str(row.get('extension', '')).strip()
                title = str(row.get('title', '')).strip()
                if not extension_no or not title: continue
                department_name = str(row.get('department', '')).strip()
                department = get_or_create_department(department_name) if department_name else None
                item = Extension.query.filter_by(extension=extension_no).first() or Extension(extension=extension_no)
                item.title = title; item.person_name = str(row.get('person_name', '')).strip() or None
                item.department = department; item.location = str(row.get('location', '')).strip() or None
                item.notes = str(row.get('notes', '')).strip() or None
                item.is_active = str(row.get('status', 'فعال')).strip() not in ('غیرفعال', 'inactive', '0', 'False')
                db.session.add(item); saved += 1
            db.session.commit()
            flash(f'{saved} داخلی با موفقیت وارد یا به‌روزرسانی شد.', 'success')
        except Exception:
            db.session.rollback(); flash('فایل قابل خواندن نیست. ستون‌های استاندارد را بررسی کنید.', 'danger')
        return redirect(url_for('admin'))

    return app


def export_headers(): return ['extension', 'title', 'person_name', 'department', 'location', 'notes', 'status']
def export_rows():
    return [{'extension': e.extension, 'title': e.title, 'person_name': e.person_name or '', 'department': e.department.name if e.department else '', 'location': e.location or '', 'notes': e.notes or '', 'status': 'فعال' if e.is_active else 'غیرفعال'} for e in Extension.query.order_by(Extension.extension).all()]
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
    font_name = 'TahomaPDF'
    try:
        pdfmetrics.getFont(font_name)
    except KeyError:
        pdfmetrics.registerFont(TTFont(font_name, font_path))
    document = SimpleDocTemplate(output, pagesize=landscape(A4), rightMargin=1.2 * cm, leftMargin=1.2 * cm, topMargin=1.2 * cm, bottomMargin=1.2 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('PersianTitle', parent=styles['Title'], fontName=font_name, fontSize=18, leading=25, alignment=TA_CENTER, textColor=colors.HexColor('#12263f'))
    heading_style = ParagraphStyle('PersianHeading', parent=styles['Heading2'], fontName=font_name, fontSize=14, leading=20, alignment=TA_RIGHT, textColor=colors.HexColor('#2166d1'), spaceBefore=10, spaceAfter=8)
    cell_style = ParagraphStyle('PersianCell', parent=styles['Normal'], fontName=font_name, fontSize=9, leading=14, alignment=TA_RIGHT)
    story = [Paragraph(pdf_text('فهرست داخلی‌های هتل'), title_style), Spacer(1, 0.35 * cm)]
    included = [d for d in departments if d.extension_entries]
    if not included:
        story.append(Paragraph(pdf_text('هیچ داخلی‌ای برای بخش انتخاب‌شده ثبت نشده است.'), heading_style))
    for index, department in enumerate(included):
        story.append(Paragraph(pdf_text(f'بخش: {department.name}'), heading_style))
        rows = [[pdf_text('وضعیت'), pdf_text('محل'), pdf_text('مسئول'), pdf_text('عنوان'), pdf_text('داخلی')]]
        entries = sorted(department.extension_entries, key=lambda item: item.extension)
        for entry in entries:
            rows.append([
                pdf_text('فعال' if entry.is_active else 'غیرفعال'),
                pdf_text(entry.location), pdf_text(entry.person_name),
                pdf_text(entry.title), pdf_text(entry.extension),
            ])
        table = Table(rows, colWidths=[3 * cm, 4.4 * cm, 4.5 * cm, 7.1 * cm, 3 * cm], repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#173f70')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#d8e2ef')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f8fc')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        story.append(table)
        if index < len(included) - 1:
            story.append(PageBreak())
    document.build(story)
def seed_data():
    if not User.query.first():
        user = User(username='admin', display_name='مدیر سیستم'); user.set_password('admin123'); db.session.add(user)
    if not Department.query.first():
        for name in ('پذیرش', 'خانه‌داری', 'رستوران', 'مدیریت', 'فنی'): db.session.add(Department(name=name))
    db.session.commit()


if __name__ == '__main__':
    create_app().run(debug=True, host='192.168.142.89', port=5000)
