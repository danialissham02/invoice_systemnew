import os
from app import app, db, User

db_url = "postgresql://invoice_user:tGYHExHLyr8xy13m7vNJudshlj7HGYQN@dpg-d7n4cf0sfn5c73drta40-a/invoice_db_s3kc"
os.environ['DATABASE_URL'] = db_url

with app.app_context():
    print("\n=== All Users in Live Database ===")
    all_users = User.query.all()
    for u in all_users:
        print(f"  {u.name} — {u.email}")
    
    # Check if bigdanial5 exists
    bigdanial = User.query.filter_by(email='bigdanial5@gmail.com').first()
    if bigdanial:
        print(f"\n✓ Found: {bigdanial.name}")
    else:
        print(f"\n✗ NOT FOUND: bigdanial5@gmail.com")