import os
import sqlite3
from werkzeug.security import generate_password_hash
from models import db, User, Classroom, ClassroomMembership, PlatformProfile, PerformanceSnapshot, RemovalRequest, AuditLog
from config import Config
from datetime import datetime, timezone

def run_database_migrations(app):
    """
    Safely creates tables and migrates legacy data if upgrading from previous versions.
    Initializes the initial admin if configured.
    """
    with app.app_context():
        # Check SQLite legacy schema if using local SQLite
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if 'sqlite:///' in db_uri and not ':memory:' in db_uri:
            db_file = db_uri.replace('sqlite:///', '')
            if os.path.exists(db_file):
                try:
                    conn = sqlite3.connect(db_file)
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user'")
                    if cursor.fetchone():
                        cursor.execute("PRAGMA table_info(user)")
                        cols = [row[1] for row in cursor.fetchall()]
                        if 'username' not in cols and 'name' in cols:
                            print("[*] Upgrading legacy 'user' table to 'legacy_profiles'...")
                            cursor.execute("ALTER TABLE user RENAME TO legacy_profiles")
                            conn.commit()
                        else:
                            # Add missing columns if upgrading modern schema
                            if 'is_email_verified' not in cols:
                                cursor.execute("ALTER TABLE user ADD COLUMN is_email_verified BOOLEAN DEFAULT 1 NOT NULL")
                            if 'otp_code' not in cols:
                                cursor.execute("ALTER TABLE user ADD COLUMN otp_code VARCHAR(128)")
                            if 'otp_expires_at' not in cols:
                                cursor.execute("ALTER TABLE user ADD COLUMN otp_expires_at DATETIME")
                            if 'otp_attempts' not in cols:
                                cursor.execute("ALTER TABLE user ADD COLUMN otp_attempts INTEGER DEFAULT 0 NOT NULL")
                            conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"Schema upgrade note: {e}")

        # Ensure all modern SQLAlchemy tables are created
        db.create_all()

        # Check and provision initial administrator, staff, and student accounts
        try:
            admin_user = User.query.filter_by(email=Config.INITIAL_ADMIN_EMAIL).first()
            if not admin_user:
                admin_user = User.query.filter_by(role='admin').first()
            if not admin_user:
                admin_email = Config.INITIAL_ADMIN_EMAIL
                admin_username = Config.INITIAL_ADMIN_USERNAME
                admin_password = Config.INITIAL_ADMIN_PASSWORD
                admin_fullname = getattr(Config, 'INITIAL_ADMIN_FULLNAME', 'Admin_User')
                
                if admin_password:
                    print(f"[*] Bootstrapping initial administrator: {admin_email}")
                    admin = User(
                        username=admin_username,
                        email=admin_email,
                        password_hash=generate_password_hash(admin_password),
                        role='admin',
                        full_name=admin_fullname,
                        is_active=True,
                        is_email_verified=True,
                        created_at=datetime.now(timezone.utc)
                    )
                    db.session.add(admin)
                    db.session.commit()
                    AuditLog.log(
                        event_type='ADMIN_BOOTSTRAP',
                        description=f'Initial administrator account bootstrapped for {admin_email}',
                        actor_id=admin.id
                    )

            # Provision sample staff account if absent
            staff_user = User.query.filter_by(email='staff@scoretracker.io').first()
            if not staff_user:
                staff_user = User(
                    username='dr_sarah',
                    email='staff@scoretracker.io',
                    password_hash=generate_password_hash('Staff@123'),
                    role='staff',
                    full_name='Dr. Sarah Jenkins',
                    is_active=True,
                    is_email_verified=True,
                    created_at=datetime.now(timezone.utc)
                )
                db.session.add(staff_user)
                db.session.commit()

            # Provision sample student account if absent
            student_user = User.query.filter_by(email='student@scoretracker.io').first()
            if not student_user:
                student_user = User(
                    username='alex_coder',
                    email='student@scoretracker.io',
                    password_hash=generate_password_hash('Student@123'),
                    role='student',
                    full_name='Alex Rivera',
                    is_active=True,
                    is_email_verified=True,
                    created_at=datetime.now(timezone.utc)
                )
                db.session.add(student_user)
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Migration error during user bootstrap setup: {e}")

        # Check legacy account migration from database_backup.db
        backup_db = os.path.join(Config.BASE_DIR, 'User_data', 'database_backup.db')
        if os.path.exists(backup_db):
            try:
                conn = sqlite3.connect(backup_db)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='account'")
                if cursor.fetchone():
                    cursor.execute("SELECT id, username, email, password_hash FROM account")
                    accounts = cursor.fetchall()
                    for acc in accounts:
                        acc_id, u_name, email, p_hash = acc
                        existing = User.query.filter((User.email == email) | (User.username == u_name)).first()
                        if not existing:
                            new_u = User(
                                username=u_name,
                                email=email,
                                password_hash=p_hash,
                                role='student',
                                full_name=u_name,
                                is_active=True
                            )
                            db.session.add(new_u)
                    db.session.commit()
                conn.close()
            except Exception as e:
                db.session.rollback()
                print(f"Legacy account migration notice: {e}")

        # Ensure all existing database records have their sensitive fields encrypted at rest
        try:
            users = User.query.all()
            dirty = False
            for u in users:
                # Triggering property assignment ensures EncryptedString bind param encrypts on commit
                if u.full_name is not None:
                    u.full_name = str(u.full_name)
                    dirty = True
                if u.student_identifier is not None:
                    u.student_identifier = str(u.student_identifier)
                    dirty = True
                if u.otp_code is not None:
                    u.otp_code = str(u.otp_code)
                    dirty = True
            if dirty:
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Encryption migration note: {e}")
