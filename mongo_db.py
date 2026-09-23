import os
import logging
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING, DESCENDING
from werkzeug.security import generate_password_hash
from config import Config

logger = logging.getLogger(__name__)

# Global MongoDB Client & Database handles
_mongo_client = None
_mongo_db = None

def get_mongo_client():
    """Initializes or returns the existing MongoDB client."""
    global _mongo_client
    if _mongo_client is None:
        uri = Config.MONGODB_URI
        if not uri:
            raise RuntimeError(
                "MONGODB_URI is not set. Please provide a valid MongoDB Atlas connection string in .env or environment variables."
            )
        _mongo_client = MongoClient(
            uri,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            socketTimeoutMS=15000,
            maxPoolSize=50,
            minPoolSize=5,
            retryWrites=True
        )
    return _mongo_client

def get_db():
    """Returns the primary application database handle."""
    global _mongo_db
    if _mongo_db is None:
        client = get_mongo_client()
        db_name = Config.MONGODB_DB or 'score_tracker'
        _mongo_db = client[db_name]
    return _mongo_db

from bson.objectid import ObjectId

def to_object_id(val):
    """Converts a value to ObjectId if valid, else returns original string."""
    if not val:
        return None
    if isinstance(val, ObjectId):
        return val
    try:
        return ObjectId(str(val))
    except Exception:
        return str(val)

def ping_mongodb():
    """Pings MongoDB Atlas to verify database connectivity. Returns (bool, str)."""
    try:
        client = get_mongo_client()
        client.admin.command('ping')
        return True, "connected"
    except Exception as e:
        logger.error(f"MongoDB Ping Failed: {e}")
        return False, "disconnected"

def ping_db():
    return ping_mongodb()

# -----------------------------------------------------------------------------
# Collection Getters
# -----------------------------------------------------------------------------
def get_users_col():
    return get_db()['users']

def get_classrooms_col():
    return get_db()['classrooms']

def get_memberships_col():
    return get_db()['classroom_memberships']

def get_profiles_col():
    return get_db()['platform_profiles']

def get_snapshots_col():
    return get_db()['performance_snapshots']

def get_removal_requests_col():
    return get_db()['removal_requests']

def get_audit_logs_col():
    return get_db()['audit_logs']

def get_password_resets_col():
    return get_db()['password_resets']

def get_bulk_imports_col():
    return get_db()['bulk_imports']

# -----------------------------------------------------------------------------
# Index Initialization
# -----------------------------------------------------------------------------
def init_indexes():
    """Creates required unique and compound indexes in MongoDB."""
    db = get_db()
    try:
        # 1. users: unique email_normalized, unique username, role index
        users = db['users']
        users.create_index([('email_normalized', ASCENDING)], unique=True, name='idx_users_email_norm_unique')
        users.create_index([('username', ASCENDING)], unique=True, name='idx_users_username_unique')
        users.create_index([('role', ASCENDING)], name='idx_users_role')
        users.create_index([('is_active', ASCENDING)], name='idx_users_is_active')

        # 2. classrooms: staff_id, status
        classrooms = db['classrooms']
        classrooms.create_index([('staff_id', ASCENDING)], name='idx_classrooms_staff')
        classrooms.create_index([('status', ASCENDING)], name='idx_classrooms_status')

        # 3. classroom_memberships: unique compound classroom_id + student_id
        memberships = db['classroom_memberships']
        memberships.create_index(
            [('classroom_id', ASCENDING), ('student_id', ASCENDING)],
            unique=True,
            name='idx_memberships_classroom_student_unique'
        )
        memberships.create_index([('student_id', ASCENDING)], name='idx_memberships_student')
        memberships.create_index([('status', ASCENDING)], name='idx_memberships_status')

        # 4. platform_profiles: unique compound user_id + platform
        profiles = db['platform_profiles']
        profiles.create_index(
            [('user_id', ASCENDING), ('platform', ASCENDING)],
            unique=True,
            name='idx_profiles_user_platform_unique'
        )
        profiles.create_index([('user_id', ASCENDING)], name='idx_profiles_user')

        # 5. performance_snapshots: unique compound user_id + platform + snapshot_date
        snapshots = db['performance_snapshots']
        snapshots.create_index(
            [('user_id', ASCENDING), ('platform', ASCENDING), ('snapshot_date', ASCENDING)],
            unique=True,
            name='idx_snapshots_user_platform_date_unique'
        )
        snapshots.create_index([('user_id', ASCENDING)], name='idx_snapshots_user')

        # 6. removal_requests: staff_id, student_id, classroom_id, status
        removals = db['removal_requests']
        removals.create_index([('classroom_id', ASCENDING)], name='idx_removals_classroom')
        removals.create_index([('status', ASCENDING)], name='idx_removals_status')

        # 7. audit_logs: actor_id, created_at
        audit_logs = db['audit_logs']
        audit_logs.create_index([('actor_id', ASCENDING)], name='idx_audit_actor')
        audit_logs.create_index([('created_at', DESCENDING)], name='idx_audit_created_at')

        # 8. password_resets: token_hash unique + TTL expiry
        resets = db['password_resets']
        resets.create_index([('token_hash', ASCENDING)], unique=True, name='idx_resets_token_hash_unique')
        resets.create_index([('expires_at', ASCENDING)], expireAfterSeconds=0, name='idx_resets_ttl')

        # 9. bulk_imports: import_id unique + TTL expiry (staging expires in 2 hours if unconfirmed)
        bulk = db['bulk_imports']
        bulk.create_index([('import_id', ASCENDING)], unique=True, name='idx_bulk_import_id_unique')
        bulk.create_index([('staff_id', ASCENDING)], name='idx_bulk_staff')
        bulk.create_index([('expires_at', ASCENDING)], expireAfterSeconds=0, name='idx_bulk_ttl')

        logger.info("MongoDB indexes verified successfully.")
    except Exception as e:
        logger.error(f"Error initializing MongoDB indexes: {e}")

# -----------------------------------------------------------------------------
# Admin Bootstrapping
# -----------------------------------------------------------------------------
def bootstrap_admin():
    """
    Seeds the initial administrator account into MongoDB if no admin exists.
    Uses environment variables (INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_PASSWORD).
    """
    admin_email = (Config.INITIAL_ADMIN_EMAIL or '').strip().lower()
    if not admin_email:
        return

    users_col = get_users_col()
    existing_admin = users_col.find_one({'role': 'admin'})
    if not existing_admin:
        raw_password = Config.INITIAL_ADMIN_PASSWORD or 'Admin@000'
        admin_doc = {
            'email': admin_email,
            'email_normalized': admin_email,
            'username': Config.INITIAL_ADMIN_USERNAME or 'admin',
            'full_name': Config.INITIAL_ADMIN_FULLNAME or 'System Administrator',
            'student_identifier': None,
            'password_hash': generate_password_hash(raw_password),
            'role': 'admin',
            'is_active': True,
            'is_email_verified': True,
            'must_change_password': False,
            'account_source': 'admin_created',
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc),
            'last_login_at': None,
            'password_changed_at': datetime.now(timezone.utc),
            'deactivated_at': None
        }
        try:
            users_col.insert_one(admin_doc)
            logger.info(f"[*] Bootstrapped initial administrator account into MongoDB: {admin_email}")
        except Exception as e:
            logger.warning(f"Admin bootstrap notice: {e}")
