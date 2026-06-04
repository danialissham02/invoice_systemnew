# Billify — Invoice Management System for Malaysian SMEs

Billify is a web-based invoice management and business intelligence platform built for small and medium enterprises (SMEs) in Malaysia. It replaces manual invoicing processes with a digital solution that includes automated insights, revenue forecasting, and professional PDF generation.

## Features

- **Invoice Management (CRUD)** — Create, view, edit, and delete invoices with dynamic line items and real-time total calculation
- **PDF Generation** — Professional invoice PDFs generated server-side using ReportLab
- **Email Sending** — Send invoices directly to clients via Resend API with PDF attachment
- **CSV Bulk Import** — Import multiple invoices at once from CSV files with drag-and-drop upload
- **Auto-Overdue Detection** — Automatically updates invoice status from Unpaid to Overdue when due date passes
- **Smart Business Insights** — Rule-based AI engine generating 5 actionable insights (revenue trend, payment health, top client, overdue alerts, service popularity) with zero external API costs
- **Revenue Forecasting** — Predicts next month's revenue using Holt's Double Exponential Smoothing
- **Dashboard Analytics** — Current month KPI cards, revenue trend chart, status doughnut chart, recent invoices
- **Monthly Reporting** — All-time KPIs, insights grid, and monthly invoice summary table
- **Admin Panel** — User management, feedback monitoring, system health metrics
- **User Feedback System** — Star ratings, category selection, and message collection
- **Role-Based Access** — Regular user and admin roles with protected routes

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python Flask |
| Database | PostgreSQL (production), SQLite (development) |
| Frontend | Custom CSS, CSS Variables, Inter Font |
| Charts | Chart.js |
| PDF Generation | ReportLab |
| Email | Resend API |
| Forecasting | Holt's Double Exponential Smoothing (Python) |
| Insights | Rule-based Python (collections.defaultdict, Counter) |
| Hosting | Render (cloud) |

## Setup

### Prerequisites
- Python 3.8+
- pip

### Installation

```bash
git clone https://github.com/YOUR-USERNAME/YOUR-REPO.git
cd invoice_system
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file or set these in your environment:

```
SECRET_KEY=your-secret-key
DATABASE_URL=your-postgresql-url
RESEND_API_KEY=your-resend-api-key
```

### Run Locally

```bash
python app.py
```

Visit `http://localhost:5000` in your browser.

### Deploy on Render

1. Push code to GitHub
2. Create a new Web Service on Render connected to your repo
3. Set environment variables (SECRET_KEY, DATABASE_URL, RESEND_API_KEY)
4. Deploy

## Project Structure

```
invoice_system/
├── app.py                  # Main application (routes, models, logic)
├── requirements.txt        # Python dependencies
├── templates/
│   ├── base.html           # Main layout (dark sidebar + light content)
│   ├── login.html          # Standalone dark login page
│   ├── register.html       # Standalone dark register page
│   ├── dashboard.html      # Dashboard with KPIs, chart, insights
│   ├── invoices.html       # Invoice list with search and filter
│   ├── invoice_form.html   # Create/edit invoice form
│   ├── invoice_view.html   # Invoice preview with status update
│   ├── reports.html        # Monthly reporting with insights grid
│   ├── import_csv.html     # CSV bulk import interface
│   ├── feedback.html       # User feedback and rating page
│   └── admin/
│       ├── base.html       # Admin panel layout
│       ├── dashboard.html  # Admin overview
│       ├── users.html      # User management
│       └── feedback.html   # Feedback management
└── static/                 # Static assets (if any)
```

## Database Schema

| Table | Description |
|-------|-------------|
| User | User accounts with name, email, password (hashed), company, admin flag |
| Invoice | Invoice records with client info, dates, status, totals, linked to User |
| InvoiceItem | Line items with description, quantity, unit price, linked to Invoice |
| Feedback | User feedback with star rating, category, message, linked to User |


## Author

**Muhammad Danial Bin Issham**

Final Year Project — Universiti Malaysia Pahang Al-Sultan Abdullah (UMPSA)

## License

This project was developed as part of a final year academic project.
