import os
import json
import base64
import requests
import numpy as np
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'invoice-system-secret-key-2024')

# Security headers
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour session timeout
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB max upload size

# Database
database_url = os.environ.get('DATABASE_URL', 'sqlite:///invoices.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Resend API
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# ── Security Headers ────────────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# ── Login attempt tracking ──────────────────────────────────────────────────────
login_attempts = {}

def check_rate_limit(email):
    now = datetime.utcnow()
    key = email.lower()
    if key not in login_attempts:
        login_attempts[key] = []
    # Remove attempts older than 15 minutes
    login_attempts[key] = [t for t in login_attempts[key] if (now - t).seconds < 900]
    return len(login_attempts[key]) >= 5

def record_login_attempt(email):
    key = email.lower()
    if key not in login_attempts:
        login_attempts[key] = []
    login_attempts[key].append(datetime.utcnow())


# ── Models ─────────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    company = db.Column(db.String(100))
    is_admin = db.Column(db.Boolean, default=False)
    is_active_account = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    invoices = db.relationship('Invoice', backref='owner', lazy=True)


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(20), unique=True, nullable=False)
    client_name = db.Column(db.String(100), nullable=False)
    client_email = db.Column(db.String(120))
    client_address = db.Column(db.Text)
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='Draft')
    subtotal = db.Column(db.Float, default=0.0)
    tax_percent = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('InvoiceItem', backref='invoice', lazy=True, cascade='all, delete-orphan')


class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Float, default=1)
    unit_price = db.Column(db.Float, default=0.0)
    amount = db.Column(db.Float, default=0.0)


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(50))
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='feedbacks')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── Helpers ─────────────────────────────────────────────────────────────────────

def generate_invoice_number(user_id):
    year = datetime.now().year
    count = Invoice.query.count() + 1
    while True:
        number = f"INV-{year}-{count:04d}"
        if not Invoice.query.filter_by(invoice_number=number).first():
            return number
        count += 1


def update_overdue_invoices():
    today = date.today()
    overdue = Invoice.query.filter(
        Invoice.due_date < today,
        Invoice.status == 'Unpaid'
    ).all()
    for inv in overdue:
        inv.status = 'Overdue'
    db.session.commit()


def generate_pdf_from_invoice(invoice, user):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    PURPLE = colors.HexColor('#7c3aed')
    PURPLE_LIGHT = colors.HexColor('#f3f0ff')
    PURPLE_BORDER = colors.HexColor('#ede9fe')
    BG_SOFT = colors.HexColor('#faf8ff')
    GRAY_BORDER = colors.HexColor('#eee9f5')
    MUTED = colors.HexColor('#8b8b9e')
    DARK = colors.HexColor('#1a1a2e')
    TEXT_MED = colors.HexColor('#6b6b80')

    # ── Header ────────────────────────────────────────────────────────────────
    brand_style = ParagraphStyle('brand', fontSize=24, textColor=PURPLE,
                                 fontName='Helvetica-Bold', leading=28)
    sender_name_style = ParagraphStyle('sender', fontSize=12, textColor=DARK,
                                       fontName='Helvetica-Bold', leading=16, spaceBefore=6)
    sender_info_style = ParagraphStyle('senderinfo', fontSize=11, textColor=MUTED, leading=15)
    inv_num_style = ParagraphStyle('invnum', fontSize=16, textColor=DARK,
                                   fontName='Helvetica-Bold', alignment=2)
    inv_label_style = ParagraphStyle('invlabel', fontSize=28, textColor=PURPLE_BORDER,
                                     fontName='Helvetica-Bold', alignment=2, leading=32)

    # Status badge color
    status_colors = {
        'Paid': '#16a34a', 'Overdue': '#dc2626',
        'Unpaid': '#d97706', 'Draft': '#8b8b9e'
    }
    status_color = status_colors.get(invoice.status, '#8b8b9e')

    header_data = [
        [
            Paragraph('Billify', brand_style),
            Paragraph('INVOICE', inv_label_style)
        ],
        [
            Paragraph(user.company or user.name, sender_name_style),
            Paragraph(invoice.invoice_number, inv_num_style)
        ],
        [
            Paragraph(user.email, sender_info_style),
            Paragraph(f'<font color="{status_color}"><b>{invoice.status}</b></font>', sender_info_style)
        ],
    ]
    header_table = Table(header_data, colWidths=[10*cm, 8*cm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.6*cm))

    # ── Bill To + Dates ───────────────────────────────────────────────────────
    label_style = ParagraphStyle('label', fontSize=9, textColor=MUTED,
                                 fontName='Helvetica-Bold', leading=12)
    client_name_style = ParagraphStyle('clientname', fontSize=13, textColor=DARK,
                                       fontName='Helvetica-Bold', leading=18)
    client_info_style = ParagraphStyle('clientinfo', fontSize=11, textColor=MUTED, leading=15)
    date_style = ParagraphStyle('date', fontSize=11, textColor=DARK, alignment=2, leading=16)

    bill_data = [
        [Paragraph('BILL TO', label_style), Paragraph('INVOICE DATES', label_style)],
        [Paragraph(invoice.client_name, client_name_style),
         Paragraph(f'Issued:  <b>{invoice.issue_date.strftime("%d %b %Y")}</b>', date_style)],
        [Paragraph(invoice.client_email or '', client_info_style),
         Paragraph(f'Due:  <font color="#7c3aed"><b>{invoice.due_date.strftime("%d %b %Y")}</b></font>', date_style)],
    ]
    if invoice.client_address:
        bill_data.append([Paragraph(invoice.client_address, client_info_style), Paragraph('', client_info_style)])

    bill_table = Table(bill_data, colWidths=[10*cm, 8*cm])
    bill_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), BG_SOFT),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING', (0,0), (-1,-1), 14),
        ('RIGHTPADDING', (0,0), (-1,-1), 14),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('ROUNDEDCORNERS', [6, 6, 6, 6]),
    ]))
    story.append(bill_table)
    story.append(Spacer(1, 0.5*cm))

    # ── Line Items ────────────────────────────────────────────────────────────
    th_style = ParagraphStyle('th', fontSize=9, textColor=MUTED, fontName='Helvetica-Bold', leading=13)
    td_style = ParagraphStyle('td', fontSize=10, textColor=DARK, leading=14)
    td_muted = ParagraphStyle('tdm', fontSize=10, textColor=TEXT_MED, leading=14, alignment=2)
    td_bold = ParagraphStyle('tdb', fontSize=10, textColor=DARK, fontName='Helvetica-Bold', leading=14, alignment=2)

    item_data = [
        [Paragraph('DESCRIPTION', th_style),
         Paragraph('QTY', ParagraphStyle('thc', parent=th_style, alignment=1)),
         Paragraph('UNIT PRICE', ParagraphStyle('thr', parent=th_style, alignment=2)),
         Paragraph('AMOUNT', ParagraphStyle('thr2', parent=th_style, alignment=2))],
    ]
    for item in invoice.items:
        qty_str = str(int(item.quantity) if item.quantity == int(item.quantity) else item.quantity)
        item_data.append([
            Paragraph(item.description, td_style),
            Paragraph(qty_str, ParagraphStyle('tdc', parent=td_muted, alignment=1)),
            Paragraph(f'RM {item.unit_price:.2f}', td_muted),
            Paragraph(f'RM {item.amount:.2f}', td_bold),
        ])

    item_table = Table(item_data, colWidths=[9*cm, 2*cm, 4*cm, 3*cm])
    item_table.setStyle(TableStyle([
        ('LINEBELOW', (0,0), (-1,0), 1.5, PURPLE_BORDER),
        ('LINEBELOW', (0,1), (-1,-1), 0.5, colors.HexColor('#f5f3fa')),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 0.3*cm))

    # ── Totals ────────────────────────────────────────────────────────────────
    total_label = ParagraphStyle('totlabel', fontSize=10, textColor=MUTED, alignment=2, leading=14)
    total_val = ParagraphStyle('totval', fontSize=10, textColor=DARK, alignment=2, leading=14)
    total_final_l = ParagraphStyle('totfinall', fontSize=13, textColor=DARK, fontName='Helvetica-Bold', alignment=2, leading=16)
    total_final_v = ParagraphStyle('totfinalv', fontSize=13, textColor=PURPLE, fontName='Helvetica-Bold', alignment=2, leading=16)

    totals_data = [
        ['', Paragraph('Subtotal', total_label), Paragraph(f'RM {invoice.subtotal:.2f}', total_val)],
    ]
    if invoice.tax_percent > 0:
        totals_data.append(['', Paragraph(f'Tax ({invoice.tax_percent}%)', total_label), Paragraph(f'RM {invoice.tax_amount:.2f}', total_val)])
    totals_data.append(['', Paragraph('Total Due', total_final_l), Paragraph(f'RM {invoice.total:.2f}', total_final_v)])

    totals_table = Table(totals_data, colWidths=[9*cm, 5*cm, 4*cm])
    last_row = len(totals_data) - 1
    totals_table.setStyle(TableStyle([
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LINEABOVE', (1, last_row), (-1, last_row), 1.5, PURPLE),
        ('TOPPADDING', (0, last_row), (-1, last_row), 10),
    ]))
    story.append(totals_table)

    # ── Notes ─────────────────────────────────────────────────────────────────
    if invoice.notes:
        story.append(Spacer(1, 0.6*cm))
        notes_label = ParagraphStyle('noteslabel', fontSize=9, textColor=PURPLE,
                                     fontName='Helvetica-Bold')
        notes_body = ParagraphStyle('notesbody', fontSize=11, textColor=TEXT_MED, leading=16)
        notes_data = [
            [Paragraph('NOTES', notes_label)],
            [Paragraph(invoice.notes, notes_body)],
        ]
        notes_table = Table(notes_data, colWidths=[18*cm])
        notes_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), BG_SOFT),
            ('LEFTPADDING', (0,0), (-1,-1), 14),
            ('RIGHTPADDING', (0,0), (-1,-1), 14),
            ('TOPPADDING', (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LINEAFTER', (-1,0), (-1,-1), 0, colors.white),
            ('LINEBEFORE', (0,0), (0,-1), 3, PURPLE),
        ]))
        story.append(notes_table)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1*cm))
    footer_style = ParagraphStyle('footer', fontSize=10, textColor=MUTED,
                                  alignment=1, leading=14)
    story.append(Paragraph('<font color="#7c3aed"><b>Billify</b></font> — Thank you for your business', footer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


# ── Auth Routes ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if check_rate_limit(email):
            flash('Too many failed login attempts. Please wait 15 minutes and try again.', 'error')
            return render_template('login.html')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            if not user.is_active_account:
                flash('Your account has been deactivated. Please contact the administrator.', 'error')
                return render_template('login.html')
            login_user(user)
            return redirect(url_for('dashboard'))

        record_login_attempt(email)
        flash('Invalid email or password.', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        company = request.form.get('company', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not name or not email or not password:
            flash('Name, email, and password are required.', 'error')
        elif len(password) < 8:
            flash('Password must be at least 8 characters long.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
        else:
            user = User(
                name=name,
                email=email,
                company=company,
                password=generate_password_hash(password)
            )
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash('Account created! Welcome aboard.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ── Dashboard ───────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    update_overdue_invoices()
    invoices = Invoice.query.filter_by(user_id=current_user.id).all()

    total_revenue = sum(i.total for i in invoices if i.status == 'Paid')
    outstanding = sum(i.total for i in invoices if i.status in ('Unpaid', 'Overdue'))
    overdue_count = sum(1 for i in invoices if i.status == 'Overdue')
    total_invoices = len(invoices)

    status_counts = {
        'Draft': sum(1 for i in invoices if i.status == 'Draft'),
        'Unpaid': sum(1 for i in invoices if i.status == 'Unpaid'),
        'Paid': sum(1 for i in invoices if i.status == 'Paid'),
        'Overdue': sum(1 for i in invoices if i.status == 'Overdue'),
    }

    recent = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).limit(5).all()

    # This month filtering
    now = datetime.now()
    this_month_invs = [i for i in invoices if i.issue_date.month == now.month and i.issue_date.year == now.year]
    last_month_num = (now.month - 1) or 12
    last_yr = now.year if now.month > 1 else now.year - 1
    last_month_invs = [i for i in invoices if i.issue_date.month == last_month_num and i.issue_date.year == last_yr]

    this_revenue = sum(i.total for i in this_month_invs if i.status == 'Paid')
    last_revenue = sum(i.total for i in last_month_invs if i.status == 'Paid')
    revenue_pct = round(((this_revenue - last_revenue) / last_revenue * 100), 1) if last_revenue > 0 else (100.0 if this_revenue > 0 else 0.0)

    this_paid = sum(1 for i in this_month_invs if i.status == 'Paid')
    this_total_count = len(this_month_invs)
    paid_pct = round(this_paid / this_total_count * 100) if this_total_count > 0 else 0

    this_outstanding = sum(i.total for i in this_month_invs if i.status in ('Unpaid', 'Overdue'))
    this_outstanding_count = sum(1 for i in this_month_invs if i.status in ('Unpaid', 'Overdue'))

    this_overdue = sum(1 for i in this_month_invs if i.status == 'Overdue')
    this_overdue_amount = sum(i.total for i in this_month_invs if i.status == 'Overdue')

    return render_template('dashboard.html',
        this_revenue=this_revenue,
        revenue_pct=revenue_pct,
        this_paid=this_paid,
        paid_pct=paid_pct,
        this_outstanding=this_outstanding,
        this_outstanding_count=this_outstanding_count,
        this_overdue=this_overdue,
        this_overdue_amount=this_overdue_amount,
        total_invoices=total_invoices,
        status_counts=json.dumps(status_counts),
        status_counts_raw=status_counts,
        recent_invoices=recent
    )


# ── Revenue Trend API ───────────────────────────────────────────────────────────

@app.route('/api/revenue-trend')
@login_required
def revenue_trend():
    from collections import defaultdict
    invoices = Invoice.query.filter_by(user_id=current_user.id, status='Paid').all()
    monthly = defaultdict(float)
    for inv in invoices:
        key = inv.issue_date.strftime('%b %Y')
        monthly[key] += inv.total
    return jsonify([{'month': k, 'revenue': round(v, 2)} for k, v in sorted(monthly.items())])


# ── Invoice Routes ──────────────────────────────────────────────────────────────

@app.route('/invoices')
@login_required
def invoices():
    update_overdue_invoices()
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '').strip()

    query = Invoice.query.filter_by(user_id=current_user.id)
    if status_filter:
        query = query.filter_by(status=status_filter)
    if search:
        query = query.filter(
            db.or_(
                Invoice.client_name.ilike(f'%{search}%'),
                Invoice.invoice_number.ilike(f'%{search}%')
            )
        )
    invoice_list = query.order_by(Invoice.created_at.desc()).all()
    return render_template('invoices.html', invoices=invoice_list, status_filter=status_filter, search=search)


@app.route('/invoices/new', methods=['GET', 'POST'])
@login_required
def new_invoice():
    if request.method == 'POST':
        try:
            descriptions = request.form.getlist('item_description[]')
            quantities = request.form.getlist('item_quantity[]')
            prices = request.form.getlist('item_price[]')

            # Calculate totals first
            tax_percent = float(request.form.get('tax_percent', 0) or 0)
            subtotal = 0
            valid_items = []
            for desc, qty, price in zip(descriptions, quantities, prices):
                if desc.strip():
                    q = float(qty or 1)
                    p = float(price or 0)
                    amt = round(q * p, 2)
                    subtotal += amt
                    valid_items.append({'description': desc.strip(), 'quantity': q, 'unit_price': p, 'amount': amt})

            subtotal = round(subtotal, 2)
            tax_amount = round(subtotal * (tax_percent / 100), 2)
            total = round(subtotal + tax_amount, 2)

            inv = Invoice(
                invoice_number=generate_invoice_number(current_user.id),
                client_name=request.form.get('client_name', '').strip(),
                client_email=request.form.get('client_email', '').strip(),
                client_address=request.form.get('client_address', '').strip(),
                issue_date=datetime.strptime(request.form.get('issue_date'), '%Y-%m-%d').date(),
                due_date=datetime.strptime(request.form.get('due_date'), '%Y-%m-%d').date(),
                tax_percent=tax_percent,
                tax_amount=tax_amount,
                subtotal=subtotal,
                total=total,
                notes=request.form.get('notes', '').strip(),
                status=request.form.get('status', 'Draft'),
                user_id=current_user.id
            )
            db.session.add(inv)
            db.session.flush()

            for item_data in valid_items:
                item = InvoiceItem(invoice_id=inv.id, **item_data)
                db.session.add(item)

            db.session.commit()
            flash('Invoice created successfully!', 'success')
            return redirect(url_for('view_invoice', id=inv.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating invoice: {str(e)}', 'error')
    return render_template('invoice_form.html', invoice=None, today=date.today().isoformat())


@app.route('/invoices/<int:id>')
@login_required
def view_invoice(id):
    update_overdue_invoices()
    inv = Invoice.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    return render_template('invoice_view.html', invoice=inv)


@app.route('/invoices/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_invoice(id):
    inv = Invoice.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        inv.client_name = request.form.get('client_name', '').strip()
        inv.client_email = request.form.get('client_email', '').strip()
        inv.client_address = request.form.get('client_address', '').strip()
        inv.issue_date = datetime.strptime(request.form.get('issue_date'), '%Y-%m-%d').date()
        inv.due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d').date()
        inv.tax_percent = float(request.form.get('tax_percent', 0) or 0)
        inv.notes = request.form.get('notes', '').strip()
        inv.status = request.form.get('status', inv.status)

        InvoiceItem.query.filter_by(invoice_id=inv.id).delete()

        descriptions = request.form.getlist('item_description[]')
        quantities = request.form.getlist('item_quantity[]')
        prices = request.form.getlist('item_price[]')

        subtotal = 0
        for desc, qty, price in zip(descriptions, quantities, prices):
            if desc.strip():
                q = float(qty or 1)
                p = float(price or 0)
                amt = q * p
                subtotal += amt
                item = InvoiceItem(invoice_id=inv.id, description=desc.strip(), quantity=q, unit_price=p, amount=amt)
                db.session.add(item)

        inv.subtotal = subtotal
        inv.tax_amount = subtotal * (inv.tax_percent / 100)
        inv.total = subtotal + inv.tax_amount
        db.session.commit()
        flash('Invoice updated!', 'success')
        return redirect(url_for('view_invoice', id=inv.id))
    return render_template('invoice_form.html', invoice=inv, today=date.today().isoformat())


@app.route('/invoices/<int:id>/delete', methods=['POST'])
@login_required
def delete_invoice(id):
    inv = Invoice.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    db.session.delete(inv)
    db.session.commit()
    flash('Invoice deleted.', 'success')
    return redirect(url_for('invoices'))


@app.route('/invoices/<int:id>/status', methods=['POST'])
@login_required
def update_status(id):
    inv = Invoice.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    new_status = request.form.get('status')
    if new_status in ('Draft', 'Unpaid', 'Paid', 'Overdue'):
        inv.status = new_status
        db.session.commit()
        flash(f'Status updated to {new_status}.', 'success')
    return redirect(url_for('view_invoice', id=id))


# ── PDF Download ────────────────────────────────────────────────────────────────

@app.route('/invoices/<int:id>/pdf')
@login_required
def download_pdf(id):
    inv = Invoice.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    pdf = generate_pdf_from_invoice(inv, current_user)
    response = make_response(pdf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename={inv.invoice_number}.pdf'
    return response


# ── Send Invoice by Email via Resend ────────────────────────────────────────────

@app.route('/invoices/<int:id>/send', methods=['POST'])
@login_required
def send_invoice(id):
    inv = Invoice.query.filter_by(id=id, user_id=current_user.id).first_or_404()

    if not inv.client_email:
        flash('This invoice has no client email address. Please edit the invoice and add one.', 'error')
        return redirect(url_for('view_invoice', id=id))

    pdf = generate_pdf_from_invoice(inv, current_user)

    try:
        pdf_base64 = base64.b64encode(pdf.read()).decode('utf-8')

        response = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'from': 'Billify <onboarding@resend.dev>',
                'to': [inv.client_email],
                'subject': f'Invoice {inv.invoice_number} from {current_user.company or current_user.name}',
                'text': f'Dear {inv.client_name},\n\nPlease find attached invoice {inv.invoice_number}.\n\nAmount Due: RM {inv.total:.2f}\nDue Date: {inv.due_date.strftime("%d %b %Y")}\n\nThank you for your business.\n\n{current_user.company or current_user.name}',
                'attachments': [{
                    'content': pdf_base64,
                    'filename': f'{inv.invoice_number}.pdf',
                }]
            }
        )

        if response.status_code == 200:
            if inv.status == 'Draft':
                inv.status = 'Unpaid'
                db.session.commit()
            flash(f'Invoice successfully sent to {inv.client_email}!', 'success')
        else:
            flash(f'Failed to send email. Error: {response.text}', 'error')

    except Exception as e:
        flash(f'Failed to send email. Error: {str(e)}', 'error')

    return redirect(url_for('view_invoice', id=id))


# ── Reports Page ───────────────────────────────────────────────────────────────

@app.route('/reports')
@login_required
def reports():
    update_overdue_invoices()
    invoices = Invoice.query.filter_by(user_id=current_user.id).all()

    from collections import defaultdict
    monthly_data = defaultdict(lambda: {'sent': 0, 'paid': 0, 'overdue': 0, 'revenue': 0.0})
    for inv in invoices:
        key = inv.issue_date.strftime('%b %Y')
        monthly_data[key]['sent'] += 1
        if inv.status == 'Paid':
            monthly_data[key]['paid'] += 1
            monthly_data[key]['revenue'] += inv.total
        if inv.status == 'Overdue':
            monthly_data[key]['overdue'] += 1

    sorted_months = sorted(monthly_data.items(),
        key=lambda x: datetime.strptime(x[0], '%b %Y'), reverse=True)

    summary = []
    for month, data in sorted_months:
        rate = round((data['paid'] / data['sent'] * 100)) if data['sent'] > 0 else 0
        summary.append({
            'month': month,
            'sent': data['sent'],
            'paid': data['paid'],
            'overdue': data['overdue'],
            'revenue': round(data['revenue'], 2),
            'rate': rate
        })

    total_revenue = sum(i.total for i in invoices if i.status == 'Paid')
    total_sent = len(invoices)
    total_paid = sum(1 for i in invoices if i.status == 'Paid')
    overall_rate = round((total_paid / total_sent * 100)) if total_sent > 0 else 0

    return render_template('reports.html',
        summary=summary,
        total_revenue=total_revenue,
        total_sent=total_sent,
        total_paid=total_paid,
        overall_rate=overall_rate
    )





# ── Revenue Forecast API ────────────────────────────────────────────────────────

@app.route('/api/revenue-forecast')
@login_required
def revenue_forecast():
    from collections import defaultdict
    invoices = Invoice.query.filter_by(user_id=current_user.id, status='Paid').all()
    monthly = defaultdict(float)
    for inv in invoices:
        key = inv.issue_date.strftime('%b %Y')
        monthly[key] += inv.total

    sorted_months = sorted(monthly.items(),
        key=lambda x: datetime.strptime(x[0], '%b %Y'))

    if len(sorted_months) < 2:
        return jsonify({'labels': [], 'actual': [], 'forecast_label': '', 'forecast_value': 0})

    labels = [m[0] for m in sorted_months]
    values = [round(m[1], 2) for m in sorted_months]

    # Holt's Double Exponential Smoothing
    # Captures both level and trend, giving more weight to recent data
    alpha = 0.6  # Level smoothing (higher = more reactive to recent data)
    beta = 0.3   # Trend smoothing (higher = faster trend adaptation)

    level = values[0]
    trend = values[1] - values[0]

    for i in range(1, len(values)):
        prev_level = level
        level = alpha * values[i] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend

    forecast_value = max(0, round(level + trend, 2))

    # Next month label
    last_date = datetime.strptime(labels[-1], '%b %Y')
    if last_date.month == 12:
        next_month = datetime(last_date.year + 1, 1, 1)
    else:
        next_month = datetime(last_date.year, last_date.month + 1, 1)
    forecast_label = next_month.strftime('%b %Y')

    return jsonify({
        'labels': labels,
        'actual': values,
        'forecast_label': forecast_label,
        'forecast_value': forecast_value
    })


# ── Top Clients API ─────────────────────────────────────────────────────────────

@app.route('/api/top-clients')
@login_required
def top_clients():
    from collections import defaultdict
    invoices = Invoice.query.filter_by(user_id=current_user.id, status='Paid').all()
    clients = defaultdict(float)
    for inv in invoices:
        clients[inv.client_name] += inv.total
    sorted_clients = sorted(clients.items(), key=lambda x: x[1], reverse=True)[:6]
    return jsonify([{'client': c, 'revenue': round(r, 2)} for c, r in sorted_clients])


# ── Monthly Summary API ──────────────────────────────────────────────────────────

@app.route('/api/monthly-summary')
@login_required
def monthly_summary():
    from collections import defaultdict
    invoices = Invoice.query.filter_by(user_id=current_user.id).all()
    monthly = defaultdict(lambda: {'sent': 0, 'paid': 0, 'overdue': 0, 'revenue': 0.0})
    for inv in invoices:
        key = inv.issue_date.strftime('%b %Y')
        monthly[key]['sent'] += 1
        if inv.status == 'Paid':
            monthly[key]['paid'] += 1
            monthly[key]['revenue'] += inv.total
        elif inv.status == 'Overdue':
            monthly[key]['overdue'] += 1
    sorted_months = sorted(monthly.items(), key=lambda x: datetime.strptime(x[0], '%b %Y'), reverse=True)[:6]
    result = []
    for month, data in sorted_months:
        rate = round((data['paid'] / data['sent'] * 100) if data['sent'] > 0 else 0)
        result.append({'month': month, 'sent': data['sent'], 'paid': data['paid'],
                       'overdue': data['overdue'], 'revenue': round(data['revenue'], 2), 'rate': rate})
    return jsonify(result)


@app.route('/import/template')
@login_required
def download_template():
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['client_name','client_email','client_address','issue_date','due_date','status','tax_percent','notes','item_description','item_quantity','item_unit_price'])
    writer.writerow(['Ahmad Sdn Bhd','ahmad@example.com','No 1 Jalan Merdeka KL','01-01-2026','31-01-2026','Unpaid','6','Thank you','Web Design Services','1','2500.00'])
    writer.writerow(['Sara Enterprise','sara@example.com','','01-02-2026','28-02-2026','Paid','0','','Monthly Maintenance','1','800.00'])
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=billify_import_template.csv'
    return response


# ── CSV Import ──────────────────────────────────────────────────────────────────

@app.route('/import', methods=['GET', 'POST'])
@login_required
def import_csv():
    if request.method == 'POST':
        file = request.files.get('csv_file')
        if not file or not file.filename.endswith('.csv'):
            flash('Please upload a valid CSV file.', 'error')
            return redirect(url_for('import_csv'))

        import csv
        import io

        stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)

        success = 0
        errors = []

        for i, row in enumerate(reader, start=2):
            try:
                # Clean keys
                row = {k.strip(): v.strip() for k, v in row.items() if k}

                client_name = row.get('client_name', '').strip()
                if not client_name:
                    errors.append(f'Row {i}: Missing client_name')
                    continue

                issue_date_str = row.get('issue_date', '').strip()
                due_date_str = row.get('due_date', '').strip()

                # Try DD-MM-YYYY first, then YYYY-MM-DD as fallback
                def parse_date(s):
                    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d'):
                        try:
                            return datetime.strptime(s, fmt).date()
                        except ValueError:
                            continue
                    raise ValueError(f"Date '{s}' must be in DD-MM-YYYY format (e.g. 31-01-2026)")

                issue_date = parse_date(issue_date_str)
                due_date = parse_date(due_date_str)

                tax_percent = float(row.get('tax_percent', 0) or 0)
                quantity = float(row.get('item_quantity', 1) or 1)
                unit_price = float(row.get('item_unit_price', 0) or 0)
                amount = round(quantity * unit_price, 2)
                subtotal = amount
                tax_amount = round(subtotal * tax_percent / 100, 2)
                total = round(subtotal + tax_amount, 2)

                inv = Invoice(
                    invoice_number=generate_invoice_number(current_user.id),
                    client_name=client_name,
                    client_email=row.get('client_email', '').strip(),
                    client_address=row.get('client_address', '').strip(),
                    issue_date=issue_date,
                    due_date=due_date,
                    status=row.get('status', 'Draft').strip() or 'Draft',
                    tax_percent=tax_percent,
                    tax_amount=tax_amount,
                    subtotal=subtotal,
                    total=total,
                    notes=row.get('notes', '').strip(),
                    user_id=current_user.id
                )
                db.session.add(inv)
                db.session.flush()

                description = row.get('item_description', 'Service').strip() or 'Service'
                item = InvoiceItem(
                    invoice_id=inv.id,
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    amount=amount
                )
                db.session.add(item)
                db.session.commit()
                success += 1

            except Exception as e:
                db.session.rollback()
                errors.append(f'Row {i}: {str(e)}')

        if success:
            flash(f'Successfully imported {success} invoice(s)!', 'success')
        if errors:
            for err in errors[:5]:
                flash(err, 'error')

        return redirect(url_for('import_csv'))

    return render_template('import_csv.html')




# ── Bulk Delete ──────────────────────────────────────────────────────────────────

@app.route('/invoices/bulk-delete', methods=['POST'])
@login_required
def bulk_delete():
    ids = request.form.getlist('invoice_ids')
    if not ids:
        flash('No invoices selected.', 'error')
        return redirect(url_for('invoices'))
    deleted = 0
    for id in ids:
        inv = Invoice.query.filter_by(id=int(id), user_id=current_user.id).first()
        if inv:
            db.session.delete(inv)
            deleted += 1
    db.session.commit()
    flash(f'Successfully deleted {deleted} invoice(s).', 'success')
    return redirect(url_for('invoices'))


# ── Feedback ─────────────────────────────────────────────────────────────────────

@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    if request.method == 'POST':
        rating = request.form.get('rating')
        category = request.form.get('category', '').strip()
        message = request.form.get('message', '').strip()
        if not rating:
            flash('Please select a rating.', 'error')
            return redirect(url_for('feedback'))
        fb = Feedback(
            user_id=current_user.id,
            rating=int(rating),
            category=category,
            message=message
        )
        db.session.add(fb)
        db.session.commit()
        flash('Thank you for your feedback!', 'success')
        return redirect(url_for('feedback'))

    feedbacks = Feedback.query.order_by(Feedback.created_at.desc()).limit(20).all()
    avg_rating = db.session.query(db.func.avg(Feedback.rating)).scalar() or 0
    total_ratings = Feedback.query.count()
    star_counts = {}
    for i in range(1, 6):
        star_counts[i] = Feedback.query.filter_by(rating=i).count()
    return render_template('feedback.html',
        feedbacks=feedbacks,
        avg_rating=round(float(avg_rating), 1),
        total_ratings=total_ratings,
        star_counts=star_counts)


# ── Admin Decorator ──────────────────────────────────────────────────────────────

from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ── Admin Routes ─────────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_invoices = Invoice.query.count()
    total_revenue = db.session.query(db.func.sum(Invoice.total)).filter_by(status='Paid').scalar() or 0
    total_feedback = Feedback.query.count()
    avg_rating = db.session.query(db.func.avg(Feedback.rating)).scalar() or 0
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    recent_feedback = Feedback.query.order_by(Feedback.created_at.desc()).limit(5).all()
    # Monthly signups for chart
    from collections import defaultdict
    all_users = User.query.all()
    monthly_signups = defaultdict(int)
    for u in all_users:
        key = u.created_at.strftime('%b %Y')
        monthly_signups[key] += 1
    sorted_signups = sorted(monthly_signups.items(), key=lambda x: datetime.strptime(x[0], '%b %Y'))
    return render_template('admin/dashboard.html',
        total_users=total_users,
        total_invoices=total_invoices,
        total_revenue=round(float(total_revenue), 2),
        total_feedback=total_feedback,
        avg_rating=round(float(avg_rating), 1),
        recent_users=recent_users,
        recent_feedback=recent_feedback,
        signup_labels=json.dumps([s[0] for s in sorted_signups]),
        signup_data=json.dumps([s[1] for s in sorted_signups])
    )


@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    search = request.args.get('search', '').strip()
    query = User.query
    if search:
        query = query.filter(db.or_(
            User.name.ilike(f'%{search}%'),
            User.email.ilike(f'%{search}%'),
            User.company.ilike(f'%{search}%')
        ))
    users = query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users, search=search)


@app.route('/admin/users/<int:id>/toggle-admin', methods=['POST'])
@login_required
@admin_required
def admin_toggle_admin(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot change your own admin status.', 'error')
        return redirect(url_for('admin_users'))
    user.is_admin = not user.is_admin
    db.session.commit()
    flash(f'{"Admin granted to" if user.is_admin else "Admin removed from"} {user.name}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:id>/toggle-active', methods=['POST'])
@login_required
@admin_required
def admin_toggle_active(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot deactivate your own account.', 'error')
        return redirect(url_for('admin_users'))
    user.is_active_account = not user.is_active_account
    db.session.commit()
    flash(f'{"Activated" if user.is_active_account else "Deactivated"} {user.name}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin_users'))
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.name} deleted.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:id>/reset-password', methods=['POST'])
@login_required
@admin_required
def admin_reset_password(id):
    user = User.query.get_or_404(id)
    new_password = request.form.get('new_password', '').strip()
    if len(new_password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('admin_users'))
    user.password = generate_password_hash(new_password)
    db.session.commit()
    flash(f'Password reset for {user.name}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/invoices')
@login_required
@admin_required
def admin_invoices():
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')
    query = Invoice.query
    if search:
        query = query.filter(db.or_(
            Invoice.client_name.ilike(f'%{search}%'),
            Invoice.invoice_number.ilike(f'%{search}%')
        ))
    if status_filter:
        query = query.filter_by(status=status_filter)
    invoices = query.order_by(Invoice.created_at.desc()).all()
    return render_template('admin/invoices.html', invoices=invoices, search=search, status_filter=status_filter)


@app.route('/admin/feedback')
@login_required
@admin_required
def admin_feedback():
    feedbacks = Feedback.query.order_by(Feedback.created_at.desc()).all()
    avg_rating = db.session.query(db.func.avg(Feedback.rating)).scalar() or 0
    total = Feedback.query.count()
    star_counts = {i: Feedback.query.filter_by(rating=i).count() for i in range(1, 6)}
    return render_template('admin/feedback.html',
        feedbacks=feedbacks,
        avg_rating=round(float(avg_rating), 1),
        total=total,
        star_counts=star_counts)


@app.route('/admin/feedback/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_feedback(id):
    fb = Feedback.query.get_or_404(id)
    db.session.delete(fb)
    db.session.commit()
    flash('Feedback deleted.', 'success')
    return redirect(url_for('admin_feedback'))



# ── Smart Business Insights ──────────────────────────────────────────────────────

from collections import Counter

class InsightEngine:
    def __init__(self, user_id):
        from collections import defaultdict
        self.invoices = Invoice.query.filter_by(user_id=user_id).all()
        self.now = datetime.now()
        self.this_month = self.now.month
        self.this_year = self.now.year
        self.last_month = (self.this_month - 1) or 12
        self.last_year = self.this_year if self.this_month > 1 else self.this_year - 1

    def get_all_insights(self):
        if not self.invoices: return []
        return [self._revenue_insight(), self._payment_insight(), self._client_insight(), self._overdue_insight(), self._service_insight()]

    def _revenue_insight(self):
        this_m = [i for i in self.invoices if i.issue_date.month == self.this_month and i.issue_date.year == self.this_year and i.status == 'Paid']
        last_m = [i for i in self.invoices if i.issue_date.month == self.last_month and i.issue_date.year == self.last_year and i.status == 'Paid']
        this_rev, last_rev = sum(i.total for i in this_m), sum(i.total for i in last_m)
        if last_rev == 0: pct, trend = (100.0 if this_rev > 0 else 0.0), ('up' if this_rev > 0 else 'flat')
        else: pct = ((this_rev - last_rev) / last_rev) * 100; trend = 'up' if pct > 0 else 'down' if pct < 0 else 'flat'
        return {'type': 'revenue', 'title': f"Revenue {'up' if trend == 'up' else 'down' if trend == 'down' else 'flat'} {abs(pct):.1f}%", 'description': f"RM {this_rev:,.2f} this month vs RM {last_rev:,.2f} last month", 'metric': f"{pct:+.1f}%", 'color': 'green' if trend == 'up' else 'red' if trend == 'down' else 'gray'}

    def _payment_insight(self):
        paid, total = len([i for i in self.invoices if i.status == 'Paid']), len(self.invoices)
        pct = (paid / total * 100) if total > 0 else 0
        if pct >= 80: label, color = 'Excellent', 'green'
        elif pct >= 60: label, color = 'Good', 'blue'
        elif pct >= 40: label, color = 'Fair', 'amber'
        else: label, color = 'Needs attention', 'red'
        return {'type': 'payment', 'title': f"Payment health: {label}", 'description': f"{paid} of {total} invoices paid ({pct:.0f}%)", 'metric': f"{pct:.0f}%", 'color': color}

    def _client_insight(self):
        from collections import defaultdict
        rev = defaultdict(float)
        for inv in self.invoices:
            if inv.status == 'Paid': rev[inv.client_name] += inv.total
        if not rev: return {'type': 'client', 'title': 'No paid invoices yet', 'description': 'Create invoices to see client insights', 'metric': '—', 'color': 'gray'}
        top_name, top_val = max(rev.items(), key=lambda x: x[1])
        return {'type': 'client', 'title': f"{top_name} is top client", 'description': f"RM {top_val:,.2f} — {(top_val/sum(rev.values())*100):.0f}% of revenue", 'metric': f"RM {top_val:,.0f}", 'color': 'purple'}

    def _overdue_insight(self):
        overdue = [i for i in self.invoices if i.status == 'Overdue']
        if not overdue: return {'type': 'overdue', 'title': 'No overdue invoices', 'description': 'All invoices are current', 'metric': '0', 'color': 'green'}
        return {'type': 'overdue', 'title': f"{len(overdue)} overdue invoice{'s' if len(overdue) > 1 else ''}", 'description': f"RM {sum(i.total for i in overdue):,.2f} outstanding", 'metric': f"RM {sum(i.total for i in overdue):,.0f}", 'color': 'red'}

    def _service_insight(self):
        this_invs = [i for i in self.invoices if i.issue_date.month == self.this_month and i.issue_date.year == self.this_year]
        services = [item.description for inv in this_invs for item in inv.items]
        if not services: return {'type': 'service', 'title': 'No services this month', 'description': 'Create invoices to see service insights', 'metric': '—', 'color': 'gray'}
        top, count = Counter(services).most_common(1)[0]
        return {'type': 'service', 'title': f"{top} is top service", 'description': f"Billed {count} time{'s' if count > 1 else ''} this month", 'metric': f"x{count}", 'color': 'blue'}


@app.route('/api/smart-insights')
@login_required
def get_smart_insights():
    engine = InsightEngine(current_user.id)
    return jsonify({'insights': engine.get_all_insights()})


# ── Run ─────────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)


@app.route('/setup-admin/<secret_key>', methods=['GET'])
def setup_admin(secret_key):
    if secret_key != 'billify_setup_2024':
        return 'Invalid', 403
    with app.app_context():
        user = User.query.order_by(User.id.desc()).first()
        if user:
            user.is_admin = True
            db.session.commit()
            return f'✓ Admin granted to {user.name}!'
        return 'No users found', 404