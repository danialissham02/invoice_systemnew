import os
from app import app, db, User

db_url = input("Paste your DATABASE_URL: ")
os.environ['DATABASE_URL'] = db_url

with app.app_context():
    print("\n=== Checking Database ===")
    try:
        all_users = User.query.all()
        print(f"✓ Connected! Found {len(all_users)} users:\n")
        for u in all_users:
            print(f"  - {u.name} ({u.email})")
    except Exception as e:
        print(f"✗ Connection failed: {e}")