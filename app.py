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

    PURPLE = colors.HexColor('#6c47ff')
    PURPLE_LIGHT = colors.HexColor('#ede9ff')
    GRAY_BG = colors.HexColor('#f9fafb')
    GRAY_BORDER = colors.HexColor('#e5e7eb')
    MUTED = colors.HexColor('#6b7280')
    DARK = colors.HexColor('#1f2937')

    # ── Header: BL logo + Billify + sender info ──────────────────────────────
    brand_style = ParagraphStyle('brand', fontSize=22, textColor=PURPLE,
                                 fontName='Helvetica-Bold', leading=26)
    sender_name_style = ParagraphStyle('sender', fontSize=12, textColor=DARK,
                                       fontName='Helvetica-Bold', leading=16, spaceBefore=6)
    sender_info_style = ParagraphStyle('senderinfo', fontSize=11, textColor=MUTED, leading=15)
    inv_num_style = ParagraphStyle('invnum', fontSize=16, textColor=DARK,
                                   fontName='Helvetica-Bold', alignment=2)
    inv_label_style = ParagraphStyle('invlabel', fontSize=28, textColor=colors.HexColor('#e5e7eb'),
                                     fontName='Helvetica-Bold', alignment=2, leading=32)

    header_data = [
        [
            Paragraph('BL  Billify', brand_style),
            Paragraph('INVOICE', inv_label_style)
        ],
        [
            Paragraph(user.company or user.name, sender_name_style),
            Paragraph(invoice.invoice_number, inv_num_style)
        ],
        [
            Paragraph(user.email, sender_info_style),
            Paragraph(f'<font color="#6c47ff"><b>{invoice.status}</b></font>', sender_info_style)
        ],
    ]
    header_table = Table(header_data, colWidths=[10*cm, 8*cm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('LINEBELOW', (0,2), (-1,2), 1, GRAY_BORDER),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.5*cm))

    # ── Bill To + Dates ───────────────────────────────────────────────────────
    label_style = ParagraphStyle('label', fontSize=9, textColor=MUTED,
                                 fontName='Helvetica-Bold', leading=12,
                                 textTransform='uppercase', spaceBefore=0)
    client_name_style = ParagraphStyle('clientname', fontSize=13, textColor=DARK,
                                       fontName='Helvetica-Bold', leading=18)
    client_info_style = ParagraphStyle('clientinfo', fontSize=11, textColor=MUTED, leading=15)
    date_style = ParagraphStyle('date', fontSize=11, textColor=DARK, alignment=2, leading=16)

    bill_data = [
        [Paragraph('Bill To', label_style), Paragraph('Invoice Dates', label_style)],
        [Paragraph(invoice.client_name, client_name_style),
         Paragraph(f'Issued:  <b>{invoice.issue_date.strftime("%d %b %Y")}</b>', date_style)],
        [Paragraph(invoice.client_email or '', client_info_style),
         Paragraph(f'Due:  <font color="#6c47ff"><b>{invoice.due_date.strftime("%d %b %Y")}</b></font>', date_style)],
    ]
    if invoice.client_address:
        bill_data.append([Paragraph(invoice.client_address, client_info_style), Paragraph('', client_info_style)])

    bill_table = Table(bill_data, colWidths=[10*cm, 8*cm])
    bill_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), GRAY_BG),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('ROUNDEDCORNERS', [6, 6, 6, 6]),
    ]))
    story.append(bill_table)
    story.append(Spacer(1, 0.5*cm))

    # ── Line Items ────────────────────────────────────────────────────────────
    item_data = [['Description', 'Qty', 'Unit Price', 'Amount']]
    for item in invoice.items:
        item_data.append([
            item.description,
            str(int(item.quantity) if item.quantity == int(item.quantity) else item.quantity),
            f'RM {item.unit_price:.2f}',
            f'RM {item.amount:.2f}'
        ])
    item_table = Table(item_data, colWidths=[9*cm, 2*cm, 4*cm, 3*cm])
    row_bgs = [GRAY_BG if i % 2 == 0 else colors.white for i in range(len(item_data) - 1)]
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PURPLE),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,1), (-1,-1), 7),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, GRAY_BG]),
        ('GRID', (0,0), (-1,-1), 0.5, GRAY_BORDER),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 0.3*cm))

    # ── Totals ────────────────────────────────────────────────────────────────
    totals_data = [
        ['', 'Subtotal', f'RM {invoice.subtotal:.2f}'],
        ['', f'Tax ({invoice.tax_percent}%)', f'RM {invoice.tax_amount:.2f}'],
        ['', 'TOTAL DUE', f'RM {invoice.total:.2f}'],
    ]
    totals_table = Table(totals_data, colWidths=[9*cm, 5*cm, 4*cm])
    totals_table.setStyle(TableStyle([
        ('FONTNAME', (1,2), (-1,2), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('FONTSIZE', (1,2), (-1,2), 12),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('TEXTCOLOR', (2,2), (2,2), PURPLE),
        ('TEXTCOLOR', (1,0), (1,1), MUTED),
        ('LINEABOVE', (1,2), (-1,2), 1.5, PURPLE),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(totals_table)

    # ── Notes ─────────────────────────────────────────────────────────────────
    if invoice.notes:
        story.append(Spacer(1, 0.5*cm))
        notes_label = ParagraphStyle('noteslabel', fontSize=9, textColor=PURPLE,
                                     fontName='Helvetica-Bold', textTransform='uppercase')
        notes_body = ParagraphStyle('notesbody', fontSize=11, textColor=DARK, leading=16)
        notes_data = [
            [Paragraph('Notes', notes_label)],
            [Paragraph(invoice.notes, notes_body)],
        ]
        notes_table = Table(notes_data, colWidths=[18*cm])
        notes_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), PURPLE_LIGHT),
            ('LEFTPADDING', (0,0), (-1,-1), 12),
            ('RIGHTPADDING', (0,0), (-1,-1), 12),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LINEAFTER', (0,0), (0,-1), 3, PURPLE),
        ]))
        story.append(notes_table)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1*cm))
    footer_style = ParagraphStyle('footer', fontSize=9, textColor=MUTED,
                                  alignment=1, leading=14)
    story.append(Paragraph('<font color="#6c47ff"><b>Billify</b></font> — Thank you for your business!', footer_style))

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

    return render_template('dashboard.html',
        total_revenue=total_revenue,
        outstanding=outstanding,
        overdue_count=overdue_count,
        total_invoices=total_invoices,
        status_counts=json.dumps(status_counts),
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
                'from': 'InvoiceFlow <onboarding@resend.dev>',
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
    return jsonify([{'client': k, 'revenue': round(v, 2)} for k, v in sorted_clients])


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

    # Linear regression using numpy
    x = np.arange(len(values))
    coeffs = np.polyfit(x, values, 1)
    forecast_value = max(0, round(float(np.polyval(coeffs, len(values))), 2))

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
    writer.writerow(['Ahmad Sdn Bhd','ahmad@example.com','No 1 Jalan Merdeka KL','2026-01-01','2026-01-31','Unpaid','6','Thank you','Web Design Services','1','2500.00'])
    writer.writerow(['Sara Enterprise','sara@example.com','','2026-02-01','2026-02-28','Paid','0','','Monthly Maintenance','1','800.00'])
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

                issue_date = datetime.strptime(row.get('issue_date', '').strip(), '%Y-%m-%d').date()
                due_date = datetime.strptime(row.get('due_date', '').strip(), '%Y-%m-%d').date()

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


# ── Reports Page ─────────────────────────────────────────────────────────────────

@app.route('/reports')
@login_required
def reports():
    invoices = Invoice.query.filter_by(user_id=current_user.id).all()
    total_invoices = len(invoices)
    total_paid = sum(1 for i in invoices if i.status == 'Paid')
    total_revenue = sum(i.total for i in invoices if i.status == 'Paid')
    total_outstanding = sum(i.total for i in invoices if i.status in ('Unpaid', 'Overdue'))
    return render_template('reports.html',
        total_invoices=total_invoices,
        total_paid=total_paid,
        total_revenue=total_revenue,
        total_outstanding=total_outstanding)


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


# ── Run ─────────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
