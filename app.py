import os
import json
import base64
import requests
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


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── Helpers ─────────────────────────────────────────────────────────────────────

def generate_invoice_number(user_id):
    count = Invoice.query.filter_by(user_id=user_id).count() + 1
    return f"INV-{datetime.now().year}-{count:04d}"


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

    header_style = ParagraphStyle('header', fontSize=20, textColor=colors.HexColor('#2563eb'), fontName='Helvetica-Bold')
    story.append(Paragraph('InvoiceFlow', header_style))
    story.append(Paragraph(user.company or user.name, styles['Normal']))
    story.append(Paragraph(user.email, styles['Normal']))
    story.append(Spacer(1, 0.5*cm))

    story.append(Paragraph(f'<b>Invoice: {invoice.invoice_number}</b>', styles['Normal']))
    story.append(Paragraph(f'Status: {invoice.status}', styles['Normal']))
    story.append(Spacer(1, 0.3*cm))

    info_data = [
        ['Bill To', 'Dates'],
        [invoice.client_name, f'Issued: {invoice.issue_date.strftime("%d %b %Y")}'],
        [invoice.client_email or '', f'Due: {invoice.due_date.strftime("%d %b %Y")}'],
        [invoice.client_address or '', ''],
    ]
    info_table = Table(info_data, colWidths=[9*cm, 9*cm])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f3f4f6')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 0.5*cm))

    item_data = [['Description', 'Qty', 'Unit Price', 'Amount']]
    for item in invoice.items:
        item_data.append([
            item.description,
            str(item.quantity),
            f'RM {item.unit_price:.2f}',
            f'RM {item.amount:.2f}'
        ])
    item_table = Table(item_data, colWidths=[9*cm, 2*cm, 4*cm, 3*cm])
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e3a8a')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f9fafb')]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 0.3*cm))

    totals_data = [
        ['', 'Subtotal', f'RM {invoice.subtotal:.2f}'],
        ['', f'Tax ({invoice.tax_percent}%)', f'RM {invoice.tax_amount:.2f}'],
        ['', 'TOTAL', f'RM {invoice.total:.2f}'],
    ]
    totals_table = Table(totals_data, colWidths=[9*cm, 5*cm, 4*cm])
    totals_table.setStyle(TableStyle([
        ('FONTNAME', (1,2), (-1,2), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('TEXTCOLOR', (2,2), (2,2), colors.HexColor('#2563eb')),
        ('LINEABOVE', (1,2), (-1,2), 1, colors.black),
        ('TOPPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(totals_table)

    if invoice.notes:
        story.append(Spacer(1, 0.5*cm))
        story.append(Paragraph('<b>Notes</b>', styles['Normal']))
        story.append(Paragraph(invoice.notes, styles['Normal']))

    story.append(Spacer(1, 1*cm))
    footer_style = ParagraphStyle('footer', fontSize=9, textColor=colors.HexColor('#9ca3af'), alignment=1)
    story.append(Paragraph('Thank you for your business!', footer_style))

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
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
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
        inv = Invoice(
            invoice_number=generate_invoice_number(current_user.id),
            client_name=request.form.get('client_name', '').strip(),
            client_email=request.form.get('client_email', '').strip(),
            client_address=request.form.get('client_address', '').strip(),
            issue_date=datetime.strptime(request.form.get('issue_date'), '%Y-%m-%d').date(),
            due_date=datetime.strptime(request.form.get('due_date'), '%Y-%m-%d').date(),
            tax_percent=float(request.form.get('tax_percent', 0) or 0),
            notes=request.form.get('notes', '').strip(),
            status=request.form.get('status', 'Draft'),
            user_id=current_user.id
        )
        db.session.add(inv)
        db.session.flush()

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
        flash('Invoice created successfully!', 'success')
        return redirect(url_for('view_invoice', id=inv.id))
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

        if response.status_code == 200 or response.status_code == 201:
            if inv.status == 'Draft':
                inv.status = 'Unpaid'
                db.session.commit()
            flash(f'Invoice successfully sent to {inv.client_email}!', 'success')
        else:
            flash(f'Failed to send email. Status: {response.status_code} Error: {response.text}', 'error')

    except Exception as e:
        flash(f'Failed to send email. Error: {str(e)}', 'error')

    return redirect(url_for('view_invoice', id=id))


# ── Run ─────────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)