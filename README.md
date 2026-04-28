#  Billify — Invoice Management System

A beginner-friendly invoice management web app built with **Flask + SQLite + Tailwind CSS**.

---

## 🚀 Quick Start (Windows + VSCode)

### Step 1 — Prerequisites
Make sure you have **Python 3.9+** installed.  
Download from https://www.python.org/downloads/  
✅ During install, tick **"Add Python to PATH"**

### Step 2 — Open in VSCode
1. Extract the ZIP into a folder, e.g. `C:\Projects\invoice_system`
2. Open **VSCode** → File → Open Folder → select `invoice_system`

### Step 3 — Set up the project
Open the terminal in VSCode (`` Ctrl + ` ``) and run these commands one by one:

```bash
# Create a virtual environment
python -m venv venv

# Activate it (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

### Step 4 — Open in browser
Go to: **http://127.0.0.1:5000**

Register a new account and start creating invoices! 🎉

---

## 📁 Project Structure

```
invoice_system/
├── app.py                  ← Main Flask app (routes, models, logic)
├── requirements.txt        ← Python packages needed
├── README.md               ← This file
├── instance/
│   └── invoices.db         ← SQLite database (auto-created on first run)
└── templates/
    ├── base.html           ← Shared layout (sidebar, navigation)
    ├── login.html          ← Login page
    ├── register.html       ← Register page
    ├── dashboard.html      ← Dashboard with charts
    ├── invoices.html       ← Invoice list with search & filter
    ├── invoice_form.html   ← Create / Edit invoice form
    └── invoice_view.html   ← View single invoice
```

---

## ✨ Features

| Feature | Details |
|---|---|
| **Auth** | Register + Login with hashed passwords |
| **Dashboard** | KPI cards (revenue, outstanding, overdue) + bar chart + doughnut chart |
| **Invoice CRUD** | Create, view, edit, delete invoices |
| **Line Items** | Dynamic add/remove rows, auto-calculated totals |
| **Tax** | Configurable tax % per invoice |
| **Status** | Draft / Unpaid / Paid / Overdue with one-click update |
| **Search & Filter** | Filter by status, search by client name or invoice number |
| **Auto-Overdue** | Unpaid invoices past due date auto-update to Overdue |
| **Currency** | Malaysian Ringgit (RM) — easy to change in templates |

---

## 🛠 Tech Stack

- **Flask** (Python) — simplest web framework for beginners
- **SQLite** — built into Python, zero installation needed
- **Tailwind CSS** — loaded via CDN, no build tools needed
- **Chart.js** — for dashboard charts
- **Flask-Login** — session management
- **Flask-SQLAlchemy** — database ORM
- **Werkzeug** — password hashing

---

## 🌍 Deployment (Free Options)

### Option A — Render.com (Recommended for beginners)
1. Push your code to GitHub (free account)
2. Go to https://render.com → New → Web Service
3. Connect your GitHub repo
4. Set:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
5. Add to `requirements.txt`: `gunicorn`
6. Click Deploy ✅

### Option B — Railway.app
1. Go to https://railway.app
2. New Project → Deploy from GitHub
3. Railway auto-detects Flask
4. Add environment variable: `SECRET_KEY=your-secret-key-here`
5. Deploy ✅

---

## 🔧 Common Issues

**"python is not recognized"**  
→ Reinstall Python and tick "Add to PATH"

**"venv\Scripts\activate is not recognized"**  
→ Run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

**Port already in use**  
→ Change `app.run(debug=True)` to `app.run(debug=True, port=5001)`

---

## 📝 Customization Tips

- **Change currency**: Search for `RM` in all templates and replace with your currency symbol
- **Add logo**: Replace the `⚡ InvoiceFlow` text in `base.html` and `invoice_view.html`
- **Change colors**: Edit `bg-blue-600` classes in templates to any Tailwind color
- **Add fields**: Add columns in `app.py` models and corresponding form fields in templates

---

Built for SMEs transitioning from manual to digital invoicing. 🚀
