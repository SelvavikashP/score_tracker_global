import os
from datetime import timedelta
from dotenv import load_dotenv

# Load .env if present
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

class Config:
    """Central application configuration for Score Tracker (MongoDB native)."""
    
    BASE_DIR = BASE_DIR
    
    # Secret Key for session signing
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(32).hex()
    
    # Environment & Debugging
    ENV = os.environ.get('FLASK_ENV', 'development')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')
    
    # Base URL for public external links and email templates (Production vs Local)
    APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')
    
    # MongoDB Database Configuration
    MONGODB_URI = os.environ.get('MONGODB_URI') or os.environ.get('MONGO_URI') or ''
    MONGODB_DB = os.environ.get('MONGODB_DB', 'score_tracker')
    
    # Default Student Password for staff provisioning and bulk import
    DEFAULT_STUDENT_PASSWORD = os.environ.get('DEFAULT_STUDENT_PASSWORD', 'Student@123')
    
    # Demo data opt-in (disabled by default in production)
    ENABLE_DEMO_DATA = os.environ.get('ENABLE_DEMO_DATA', 'False').lower() in ('true', '1', 't')
    
    # Session & Security Settings (1-hour inactivity timeout)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)
    SESSION_INACTIVITY_TIMEOUT_SECONDS = int(os.environ.get('SESSION_INACTIVITY_TIMEOUT_SECONDS', 3600))  # 1 hour (3600s)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1', 't')
    
    # Initial Admin Bootstrap (Configured via environment variables)
    INITIAL_ADMIN_EMAIL = os.environ.get('INITIAL_ADMIN_EMAIL', 'selvavikash000@gmail.com')
    INITIAL_ADMIN_USERNAME = os.environ.get('INITIAL_ADMIN_USERNAME', 'Selva vikash')
    INITIAL_ADMIN_PASSWORD = os.environ.get('INITIAL_ADMIN_PASSWORD', 'Admin@000')
    INITIAL_ADMIN_FULLNAME = os.environ.get('INITIAL_ADMIN_FULLNAME', 'System Admin')
    
    # Platform Sync & Caching Settings
    SYNC_CACHE_MINUTES = int(os.environ.get('SYNC_CACHE_MINUTES', 30))
    MAX_CONCURRENT_SYNC_WORKERS = int(os.environ.get('MAX_CONCURRENT_SYNC_WORKERS', 4))
    
    # Excel Exports Directory
    EXCEL_EXPORT_DIR = os.path.join(BASE_DIR, 'User_data', 'exports')
    
    # Scheduler Settings
    SCHEDULER_API_ENABLED = False
    
    # Password Reset Token Settings
    PASSWORD_RESET_EXPIRE_MINUTES = int(os.environ.get('PASSWORD_RESET_EXPIRE_MINUTES', 15))
    
    # Email & Notification Settings
    REQUIRE_EMAIL_VERIFICATION = os.environ.get('REQUIRE_EMAIL_VERIFICATION', 'False').lower() in ('true', '1', 't')
    OTP_EXPIRE_MINUTES = int(os.environ.get('OTP_EXPIRE_MINUTES', 10))
    SMTP_HOST = os.environ.get('SMTP_HOST')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USER = os.environ.get('SMTP_USER')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'True').lower() in ('true', '1', 't')
    SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL', 'no-reply@scoretracker.io')
