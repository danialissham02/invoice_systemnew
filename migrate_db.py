import os
from app import app, db
from sqlalchemy import text

db_url = input("Paste your DATABASE_URL: ")
os.environ['DATABASE_URL'] = db_url

with app.app_context():
    try:
        # Add is_admin column
        db.session.execute(text('ALTER TABLE "user" ADD COLUMN is_admin BOOLEAN DEFAULT FALSE'))
        print("✓ Added is_admin column")
    except Exception as e:
        print(f"is_admin: {e}")
    
    try:
        # Add is_active_account column
        db.session.execute(text('ALTER TABLE "user" ADD COLUMN is_active_account BOOLEAN DEFAULT TRUE'))
        print("✓ Added is_active_account column")
    except Exception as e:
        print(f"is_active_account: {e}")
    
    db.session.commit()
    print("✓ Database migration complete!")