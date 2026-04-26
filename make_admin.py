import os
from app import app, db, User

db_url = input("Paste your DATABASE_URL: ")
os.environ['DATABASE_URL'] = db_url

with app.app_context():
    email = input("Enter your email: ")
    user = User.query.filter_by(email=email).first()
    if user:
        user.is_admin = True
        db.session.commit()
        print(f'✓ Admin granted to {user.name}')
    else:
        print('✗ User not found')