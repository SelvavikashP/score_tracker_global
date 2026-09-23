import os
from datetime import timedelta
from dotenv import load_dotenv

# Load .env if present
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

class Config:
    """Central application configuration."""
    
    BASE_DIR = BASE_DIR
    
    # Secret Key for session signing
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32).hex()
    
    # Environment & Debugging
    ENV = os.environ.get('FLASK_ENV', 'development')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')
    
    # Database Configuration (PostgreSQL in production, SQLite for local dev)
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        # Normalize postgres:// to postgresql:// for modern SQLAlchemy
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        SQLALCHEMY_DATABASE_URI = database_url
    else:
        user_data_dir = os.path.join(BASE_DIR, 'User_data')
        os.makedirs(user_data_dir, exist_ok=True)
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(user_data_dir, 'database.db')}"
        
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }
    
    # Session & Security Settings
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1', 't')
    
    # Initial Admin Bootstrap (Configured exclusively via environment variables or defaults)
    INITIAL_ADMIN_EMAIL = os.environ.get('INITIAL_ADMIN_EMAIL', 'selvavikash000@gmail.com')
    INITIAL_ADMIN_USERNAME = os.environ.get('INITIAL_ADMIN_USERNAME', 'Selva vikash')
    INITIAL_ADMIN_PASSWORD = os.environ.get('INITIAL_ADMIN_PASSWORD', 'Admin@000')
    INITIAL_ADMIN_FULLNAME = os.environ.get('INITIAL_ADMIN_FULLNAME', 'Admin_User')
    
    # Platform Sync & Caching Settings
    SYNC_CACHE_MINUTES = int(os.environ.get('SYNC_CACHE_MINUTES', 30))
    MAX_CONCURRENT_SYNC_WORKERS = int(os.environ.get('MAX_CONCURRENT_SYNC_WORKERS', 4))
    
    # Excel Exports Directory
    EXCEL_EXPORT_DIR = os.path.join(BASE_DIR, 'User_data', 'exports')
    
    # Scheduler Settings
    SCHEDULER_API_ENABLED = False
    
    # Email & Notification Settings (Email verification disabled by default; registration welcome emails enabled)
    REQUIRE_EMAIL_VERIFICATION = os.environ.get('REQUIRE_EMAIL_VERIFICATION', 'False').lower() in ('true', '1', 't')
    OTP_EXPIRE_MINUTES = int(os.environ.get('OTP_EXPIRE_MINUTES', 10))
    SMTP_HOST = os.environ.get('SMTP_HOST')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USER = os.environ.get('SMTP_USER')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'True').lower() in ('true', '1', 't')
    SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL', 'no-reply@scoretracker.io')
