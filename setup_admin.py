from app import app, db, User

with app.app_context():
    user = User.query.filter_by(email='danialissham2@gmail.com').first()
    if user:
        user.is_admin = True
        db.session.commit()
        print('✓ Admin granted to', user.name)
    else:
        print('User not found')