from app import app, db, User

@app.route('/setup-admin/<secret_key>', methods=['GET'])
def setup_admin(secret_key):
    if secret_key != 'billify_setup_2024':
        return 'Invalid key', 403
    
    with app.app_context():
        # Get the most recent user (should be you)
        user = User.query.order_by(User.id.desc()).first()
        if user:
            user.is_admin = True
            db.session.commit()
            return f'✓ Admin granted to {user.name} ({user.email})! You can now delete this setup page.'
        return 'No users found', 404

if __name__ == '__main__':
    app.run()
