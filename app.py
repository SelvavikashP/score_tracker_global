from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from models import db, User, Account
from api_utils import fetch_user_data
from excel_utils import update_excel, get_excel_path
from flask_apscheduler import APScheduler
import os
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from functools import wraps

app = Flask(__name__)

# Use /tmp for DB on Render (read-only filesystem elsewhere)
if os.environ.get('RENDER'):
    DB_PATH = '/tmp/database.db'
else:
    # Use instance folder locally
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    DB_DIR = os.path.join(BASE_DIR, 'instance')
    os.makedirs(DB_DIR, exist_ok=True)
    DB_PATH = os.path.join(DB_DIR, 'database.db')

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key'

db.init_app(app)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'account_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
scheduler = APScheduler()

# Ensure DB tables exist when app is imported (works with gunicorn on Render)
with app.app_context():
    db.create_all()

def update_all_users():
    with app.app_context():
        users = User.query.all()
        for user in users:
            data = fetch_user_data(user.profile_url, user.platform)
            if data:
                user.rating = data.get('rating', 0)
                user.rank = data.get('rank', 'Unrated')
                user.global_rank = data.get('global_rank', 0)
                user.country_rank = data.get('country_rank', 0)
                user.recent_problems = data.get('recent_problems', 0)
                user.total_contests = data.get('total_contests', 0)
                user.last_updated = datetime.utcnow()
        db.session.commit()
        update_excel(users)
        print(f"Daily update completed: {datetime.now()}")

@app.route('/')
@login_required
def index():
    account_id = session.get('account_id')
    users = User.query.filter_by(account_id=account_id).order_by(User.rating.desc()).all()
    logged_in = True
    username = session.get('username')
    return render_template('index.html', users=users, logged_in=logged_in, username=username)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if not username or not email or not password:
            flash('All fields are required', 'danger')
            return redirect(url_for('signup'))

        existing_account = Account.query.filter_by(email=email).first()
        if existing_account:
            flash('An account with that email already exists.', 'warning')
            return redirect(url_for('signup'))

        hashed_password = generate_password_hash(password)
        new_account = Account(username=username, email=email, password_hash=hashed_password)
        db.session.add(new_account)
        db.session.commit()

        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        account = Account.query.filter_by(email=email).first()
        if account and check_password_hash(account.password_hash, password):
            session['account_id'] = account.id
            session['username'] = account.username
            flash('Logged in successfully.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('account_id', None)
    session.pop('username', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        platform = request.form.get('platform')
        profile_url = request.form.get('profile_url')

        if not name or not profile_url:
            flash('All fields are required!', 'danger')
            return redirect(url_for('register'))

        # Check if user already exists
        existing_user = User.query.filter_by(profile_url=profile_url).first()
        if existing_user:
            flash('Profile URL already registered!', 'warning')
            return redirect(url_for('register'))

        # Fetch initial data
        data = fetch_user_data(profile_url, platform)
        if not data:
            flash('Invalid Profile URL or could not fetch data.', 'danger')
            return redirect(url_for('register'))

        new_user = User(
            account_id=session.get('account_id'),
            name=name,
            platform=platform,
            profile_url=profile_url,
            rating=data.get('rating', 0),
            rank=data.get('rank', 'Unrated'),
            global_rank=data.get('global_rank', 0),
            country_rank=data.get('country_rank', 0),
            recent_problems=data.get('recent_problems', 0),
            total_contests=data.get('total_contests', 0)
        )
        db.session.add(new_user)
        db.session.commit()

        # Update Excel with only this account's profiles
        account_users = User.query.filter_by(account_id=session.get('account_id')).all()
        update_excel(account_users)

        flash('Registration successful!', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')

@app.route('/refresh/<int:user_id>')
@login_required
def refresh(user_id):
    user = User.query.get_or_404(user_id)
    data = fetch_user_data(user.profile_url, user.platform)
    if data:
        user.rating = data.get('rating', 0)
        user.rank = data.get('rank', 'Unrated')
        user.global_rank = data.get('global_rank', 0)
        user.country_rank = data.get('country_rank', 0)
        user.recent_problems = data.get('recent_problems', 0)
        user.total_contests = data.get('total_contests', 0)
        user.last_updated = datetime.utcnow()
        db.session.commit()
        account_users = User.query.filter_by(account_id=session.get('account_id')).all()
        update_excel(account_users)
        flash(f'Updated data for {user.name}', 'success')
    else:
        flash('Failed to update data.', 'danger')
    return redirect(url_for('index'))

@app.route('/delete/<int:user_id>')
@login_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    name = user.name
    db.session.delete(user)
    db.session.commit()
    # Update Excel after deletion
    account_users = User.query.filter_by(account_id=session.get('account_id')).all()
    update_excel(account_users)
    flash(f'User {name} deleted successfully.', 'success')
    return redirect(url_for('index'))

@app.route('/download')
@login_required
def download():
    # Always regenerate the Excel file fresh with the current user's data
    account_users = User.query.filter_by(account_id=session.get('account_id')).all()
    if not account_users:
        flash('No profiles added yet. Add some platform profiles first.', 'info')
        return redirect(url_for('index'))
    update_excel(account_users)
    path = get_excel_path()
    return send_file(path, as_attachment=True, download_name=f"{session.get('username', 'export')}_scores.xlsx")

if __name__ == '__main__':
    # Setup scheduler only when running locally (not under gunicorn)
    scheduler.add_job(id='daily_update', func=update_all_users, trigger='interval', days=1)
    scheduler.start()
    app.run(debug=True)
