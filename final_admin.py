import os
os.environ['DATABASE_URL'] = "postgresql://invoice_user:tGYHExHLyr8xy13m7vNJudshlj7HGYQN@dpg-d7n4cf0sfn5c73drta40-a/invoice_db_s3kc"

from app import app, db, User

with app.app_context():
    print("\n=== Users in Live Database ===")
    all_users = User.query.all()
    print(f"Found {len(all_users)} users:")
    for u in all_users:
        print(f"  - {u.name} ({u.email})")
    
    email = input("\nEnter email to make admin: ")
    user = User.query.filter_by(email=email).first()
    if user:
        user.is_admin = True
        db.session.commit()
        print(f'✓ Admin granted to {user.name}!')
    else:
        print(f'✗ User not found')