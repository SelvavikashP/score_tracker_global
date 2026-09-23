import secrets
import hashlib
from datetime import datetime, timezone, timedelta, date
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import DESCENDING, ASCENDING
from mongo_db import (
    get_users_col, get_classrooms_col, get_memberships_col,
    get_profiles_col, get_snapshots_col, get_removal_requests_col,
    get_audit_logs_col, get_password_resets_col, get_bulk_imports_col
)
from config import Config

def utc_now():
    return datetime.now(timezone.utc)

def normalize_email(email):
    """Trims whitespace and converts email to lowercase."""
    return (email or '').strip().lower()

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

# =============================================================================
# USER MODEL & REPOSITORY
# =============================================================================
class User:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.id = str(self._id) if self._id is not None else None
        self.email = doc.get('email', '')
        self.email_normalized = doc.get('email_normalized', normalize_email(self.email))
        self.username = doc.get('username', '')
        self.full_name = doc.get('full_name', '')
        self.student_identifier = doc.get('student_identifier')
        self.password_hash = doc.get('password_hash', '')
        self.role = doc.get('role', 'student')
        self.is_active = doc.get('is_active', True)
        self.is_email_verified = doc.get('is_email_verified', True)
        self.must_change_password = doc.get('must_change_password', False)
        self.account_source = doc.get('account_source', 'admin_created')
        self.created_at = doc.get('created_at', utc_now())
        self.updated_at = doc.get('updated_at', utc_now())
        self.last_login_at = doc.get('last_login_at')
        self.password_changed_at = doc.get('password_changed_at')
        self.deactivated_at = doc.get('deactivated_at')
        self.otp_code = doc.get('otp_code')
        self.otp_expires_at = doc.get('otp_expires_at')
        self.otp_attempts = doc.get('otp_attempts', 0)

    def check_password(self, candidate_password):
        if not self.password_hash or not candidate_password:
            return False
        return check_password_hash(self.password_hash, candidate_password)

    def set_password(self, new_password):
        self.password_hash = generate_password_hash(new_password)
        self.must_change_password = False
        self.password_changed_at = utc_now()
        self.updated_at = utc_now()
        get_users_col().update_one(
            {'_id': self._id},
            {'$set': {
                'password_hash': self.password_hash,
                'must_change_password': False,
                'password_changed_at': self.password_changed_at,
                'updated_at': self.updated_at
            }}
        )

    def update_last_login(self):
        self.last_login_at = utc_now()
        get_users_col().update_one(
            {'_id': self._id},
            {'$set': {'last_login_at': self.last_login_at}}
        )

    def generate_otp(self, expires_in_minutes=10):
        code = f"{secrets.randbelow(900000) + 100000}"
        self.otp_code = code
        self.otp_expires_at = utc_now() + timedelta(minutes=expires_in_minutes)
        self.otp_attempts = 0
        get_users_col().update_one(
            {'_id': self._id},
            {'$set': {
                'otp_code': self.otp_code,
                'otp_expires_at': self.otp_expires_at,
                'otp_attempts': 0
            }}
        )
        return code

    def verify_otp(self, candidate_code):
        if not self.otp_code or not self.otp_expires_at:
            return False, "No active OTP found. Please request a new verification code."
        expires_at = self.otp_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if utc_now() > expires_at:
            self.clear_otp()
            return False, "Your OTP has expired. Please request a new code."
        if self.otp_attempts >= 5:
            self.clear_otp()
            return False, "Maximum verification attempts exceeded. Please request a new OTP."
        self.otp_attempts += 1
        if candidate_code.strip() == self.otp_code.strip():
            self.is_email_verified = True
            self.clear_otp(is_verified=True)
            return True, "Email verified successfully!"
        get_users_col().update_one({'_id': self._id}, {'$set': {'otp_attempts': self.otp_attempts}})
        return False, "Invalid verification code. Please check and try again."

    def clear_otp(self, is_verified=None):
        update_data = {'otp_code': None, 'otp_expires_at': None, 'otp_attempts': 0}
        if is_verified is not None:
            update_data['is_email_verified'] = is_verified
        get_users_col().update_one({'_id': self._id}, {'$set': update_data})

    def get_profiles(self):
        return PlatformProfile.find_by_user_id(self.id)

    def get_classrooms(self):
        memberships = ClassroomMembership.find_by_student_id(self.id, status='active')
        classrooms = []
        for m in memberships:
            c = Classroom.find_by_id(m.classroom_id)
            if c and c.status == 'active':
                classrooms.append(c)
        return classrooms

    def to_dict(self, include_private=False):
        data = {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name or self.username,
            "role": self.role,
            "is_active": self.is_active,
            "is_email_verified": self.is_email_verified,
            "must_change_password": self.must_change_password,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }
        if include_private:
            data.update({
                "email": self.email,
                "student_identifier": self.student_identifier,
                "last_login_at": self.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_login_at else None
            })
        return data

    @classmethod
    def find_by_id(cls, user_id):
        if not user_id:
            return None
        col = get_users_col()
        doc = col.find_one({'_id': to_object_id(user_id)})
        if not doc and isinstance(user_id, int):
            doc = col.find_one({'legacy_id': user_id})
        return cls(doc) if doc else None

    @classmethod
    def find_by_email(cls, email):
        if not email:
            return None
        norm = normalize_email(email)
        col = get_users_col()
        doc = col.find_one({'email_normalized': norm})
        return cls(doc) if doc else None

    @classmethod
    def find_by_username(cls, username):
        if not username:
            return None
        col = get_users_col()
        doc = col.find_one({'username': username.strip()})
        return cls(doc) if doc else None

    @classmethod
    def find_all(cls, filter_dict=None, sort=None):
        col = get_users_col()
        q = filter_dict or {}
        cursor = col.find(q)
        if sort:
            cursor = cursor.sort(sort)
        return [cls(d) for d in cursor]

    @classmethod
    def count(cls, filter_dict=None):
        return get_users_col().count_documents(filter_dict or {})

    @classmethod
    def create(cls, data):
        col = get_users_col()
        raw_email = data.get('email', '').strip()
        norm_email = normalize_email(raw_email)
        doc = {
            'email': raw_email,
            'email_normalized': norm_email,
            'username': data.get('username', raw_email.split('@')[0]),
            'full_name': data.get('full_name') or data.get('username', ''),
            'student_identifier': data.get('student_identifier'),
            'password_hash': data.get('password_hash') or generate_password_hash(Config.DEFAULT_STUDENT_PASSWORD),
            'role': data.get('role', 'student'),
            'is_active': data.get('is_active', True),
            'is_email_verified': data.get('is_email_verified', True),
            'must_change_password': data.get('must_change_password', False),
            'account_source': data.get('account_source', 'admin_created'),
            'created_at': data.get('created_at', utc_now()),
            'updated_at': utc_now(),
            'last_login_at': None,
            'password_changed_at': None if data.get('must_change_password') else utc_now(),
            'deactivated_at': None
        }
        res = col.insert_one(doc)
        doc['_id'] = res.inserted_id
        return cls(doc)

    @classmethod
    def update_by_id(cls, user_id, update_fields):
        update_fields['updated_at'] = utc_now()
        get_users_col().update_one({'_id': to_object_id(user_id)}, {'$set': update_fields})

    @classmethod
    def delete_by_id(cls, user_id):
        uid = str(user_id)
        # Cascade delete related memberships, profiles, snapshots
        get_users_col().delete_one({'_id': to_object_id(user_id)})
        get_memberships_col().delete_many({'student_id': uid})
        get_profiles_col().delete_many({'user_id': uid})
        get_snapshots_col().delete_many({'user_id': uid})
        get_removal_requests_col().delete_many({'$or': [{'student_id': uid}, {'staff_id': uid}]})

# =============================================================================
# CLASSROOM MODEL & REPOSITORY
# =============================================================================
class Classroom:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.id = str(self._id) if self._id is not None else None
        self.name = doc.get('name', '')
        self.description = doc.get('description', '')
        self.staff_id = str(doc.get('staff_id', ''))
        self.status = doc.get('status', 'active')
        self.created_at = doc.get('created_at', utc_now())
        self.updated_at = doc.get('updated_at', utc_now())
        self.archived_at = doc.get('archived_at')

    def active_students_count(self):
        return get_memberships_col().count_documents({
            'classroom_id': self.id,
            'status': 'active'
        })

    def get_staff(self):
        return User.find_by_id(self.staff_id)

    def get_memberships(self, status='active'):
        return ClassroomMembership.find_by_classroom_id(self.id, status=status)

    @classmethod
    def find_by_id(cls, classroom_id):
        if not classroom_id:
            return None
        col = get_classrooms_col()
        doc = col.find_one({'_id': to_object_id(classroom_id)})
        if not doc and isinstance(classroom_id, int):
            doc = col.find_one({'legacy_id': classroom_id})
        return cls(doc) if doc else None

    @classmethod
    def find_by_staff_id(cls, staff_id, status=None):
        q = {'staff_id': str(staff_id)}
        if status:
            q['status'] = status
        col = get_classrooms_col()
        return [cls(d) for d in col.find(q).sort('created_at', DESCENDING)]

    @classmethod
    def find_all(cls, filter_dict=None, sort=None):
        col = get_classrooms_col()
        q = filter_dict or {}
        cursor = col.find(q)
        if sort:
            cursor = cursor.sort(sort)
        else:
            cursor = cursor.sort('created_at', DESCENDING)
        return [cls(d) for d in cursor]

    @classmethod
    def count(cls, filter_dict=None):
        return get_classrooms_col().count_documents(filter_dict or {})

    @classmethod
    def create(cls, name, staff_id, description=""):
        doc = {
            'name': name.strip(),
            'description': description.strip(),
            'staff_id': str(staff_id),
            'status': 'active',
            'created_at': utc_now(),
            'updated_at': utc_now(),
            'archived_at': None
        }
        res = get_classrooms_col().insert_one(doc)
        doc['_id'] = res.inserted_id
        return cls(doc)

    @classmethod
    def delete_by_id(cls, classroom_id):
        cid = str(classroom_id)
        get_classrooms_col().delete_one({'_id': to_object_id(classroom_id)})
        get_memberships_col().delete_many({'classroom_id': cid})
        get_removal_requests_col().delete_many({'classroom_id': cid})

# =============================================================================
# CLASSROOM MEMBERSHIP MODEL & REPOSITORY
# =============================================================================
class ClassroomMembership:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.id = str(self._id) if self._id is not None else None
        self.classroom_id = str(doc.get('classroom_id', ''))
        self.student_id = str(doc.get('student_id', ''))
        self.status = doc.get('status', 'active')  # 'active', 'removed'
        self.joined_at = doc.get('joined_at', utc_now())
        self.updated_at = doc.get('updated_at', utc_now())
        self.removed_at = doc.get('removed_at')

    @property
    def student(self):
        return User.find_by_id(self.student_id)

    @property
    def classroom(self):
        return Classroom.find_by_id(self.classroom_id)

    @classmethod
    def find_by_id(cls, membership_id):
        if not membership_id:
            return None
        doc = get_memberships_col().find_one({'_id': to_object_id(membership_id)})
        return cls(doc) if doc else None

    @classmethod
    def find_one(cls, classroom_id, student_id):
        doc = get_memberships_col().find_one({
            'classroom_id': str(classroom_id),
            'student_id': str(student_id)
        })
        return cls(doc) if doc else None

    @classmethod
    def find_by_classroom_id(cls, classroom_id, status=None):
        q = {'classroom_id': str(classroom_id)}
        if status:
            q['status'] = status
        return [cls(d) for d in get_memberships_col().find(q).sort('joined_at', DESCENDING)]

    @classmethod
    def find_by_student_id(cls, student_id, status=None):
        q = {'student_id': str(student_id)}
        if status:
            q['status'] = status
        return [cls(d) for d in get_memberships_col().find(q).sort('joined_at', DESCENDING)]

    @classmethod
    def create_or_activate(cls, classroom_id, student_id):
        col = get_memberships_col()
        cid = str(classroom_id)
        sid = str(student_id)
        existing = col.find_one({'classroom_id': cid, 'student_id': sid})
        if existing:
            if existing.get('status') != 'active':
                col.update_one(
                    {'_id': existing['_id']},
                    {'$set': {'status': 'active', 'updated_at': utc_now(), 'removed_at': None}}
                )
                existing['status'] = 'active'
            return cls(existing), False  # Already existed
        else:
            doc = {
                'classroom_id': cid,
                'student_id': sid,
                'status': 'active',
                'joined_at': utc_now(),
                'updated_at': utc_now(),
                'removed_at': None
            }
            res = col.insert_one(doc)
            doc['_id'] = res.inserted_id
            return cls(doc), True  # Newly created

# =============================================================================
# PLATFORM PROFILE MODEL & REPOSITORY
# =============================================================================
class PlatformProfile:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.id = str(self._id) if self._id is not None else None
        self.user_id = str(doc.get('user_id', ''))
        self.platform = doc.get('platform', '')
        self.handle = doc.get('handle', '')
        self.profile_url = doc.get('profile_url', '')
        self.rating = int(doc.get('rating', 0) or 0)
        self.rank = doc.get('rank', 'Unrated')
        self.global_rank = int(doc.get('global_rank', 0) or 0)
        self.country_rank = int(doc.get('country_rank', 0) or 0)
        self.recent_problems = int(doc.get('recent_problems', 0) or 0)
        self.total_contests = int(doc.get('total_contests', 0) or 0)
        self.last_synced_at = doc.get('last_synced_at')
        self.sync_status = doc.get('sync_status', 'pending')
        self.sync_error = doc.get('sync_error')

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "platform": self.platform,
            "handle": self.handle,
            "profile_url": self.profile_url,
            "rating": self.rating,
            "rank": self.rank,
            "global_rank": self.global_rank,
            "country_rank": self.country_rank,
            "recent_problems": self.recent_problems,
            "total_contests": self.total_contests,
            "last_synced_at": self.last_synced_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_synced_at else None,
            "sync_status": self.sync_status,
            "sync_error": self.sync_error
        }

    @classmethod
    def find_by_id(cls, profile_id):
        if not profile_id:
            return None
        doc = get_profiles_col().find_one({'_id': to_object_id(profile_id)})
        return cls(doc) if doc else None

    @classmethod
    def find_by_user_id(cls, user_id):
        return [cls(d) for d in get_profiles_col().find({'user_id': str(user_id)})]

    @classmethod
    def find_one(cls, user_id, platform):
        doc = get_profiles_col().find_one({
            'user_id': str(user_id),
            'platform': platform
        })
        return cls(doc) if doc else None

    @classmethod
    def upsert(cls, user_id, platform, data):
        col = get_profiles_col()
        uid = str(user_id)
        doc = {
            'user_id': uid,
            'platform': platform,
            'handle': data.get('handle', ''),
            'profile_url': data.get('profile_url', ''),
            'rating': int(data.get('rating', 0) or 0),
            'rank': data.get('rank', 'Unrated'),
            'global_rank': int(data.get('global_rank', 0) or 0),
            'country_rank': int(data.get('country_rank', 0) or 0),
            'recent_problems': int(data.get('recent_problems', 0) or 0),
            'total_contests': int(data.get('total_contests', 0) or 0),
            'last_synced_at': data.get('last_synced_at', utc_now()),
            'sync_status': data.get('sync_status', 'success'),
            'sync_error': data.get('sync_error')
        }
        res = col.find_one_and_update(
            {'user_id': uid, 'platform': platform},
            {'$set': doc},
            upsert=True,
            return_document=True
        )
        return cls(res or doc)

    @classmethod
    def delete_by_id(cls, profile_id):
        get_profiles_col().delete_one({'_id': to_object_id(profile_id)})

# =============================================================================
# PERFORMANCE SNAPSHOT MODEL & REPOSITORY
# =============================================================================
class PerformanceSnapshot:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.id = str(self._id) if self._id is not None else None
        self.user_id = str(doc.get('user_id', ''))
        self.platform = doc.get('platform', '')
        self.snapshot_date = doc.get('snapshot_date')
        self.rating = int(doc.get('rating', 0) or 0)
        self.rating_delta = int(doc.get('rating_delta', 0) or 0)
        self.rank = doc.get('rank', 'Unrated')
        self.global_rank = int(doc.get('global_rank', 0) or 0)
        self.country_rank = int(doc.get('country_rank', 0) or 0)
        self.problems_solved = int(doc.get('problems_solved', 0) or 0)
        self.problems_solved_delta = int(doc.get('problems_solved_delta', 0) or 0)
        self.contests = int(doc.get('contests', 0) or 0)
        self.contests_delta = int(doc.get('contests_delta', 0) or 0)
        self.calculated_score = float(doc.get('calculated_score', 0.0) or 0.0)
        self.created_at = doc.get('created_at', utc_now())

    @classmethod
    def find_by_user_id(cls, user_id, limit=30):
        return [cls(d) for d in get_snapshots_col().find({'user_id': str(user_id)}).sort('snapshot_date', DESCENDING).limit(limit)]

    @classmethod
    def find_latest(cls, user_id, platform):
        doc = get_snapshots_col().find_one(
            {'user_id': str(user_id), 'platform': platform},
            sort=[('snapshot_date', DESCENDING)]
        )
        return cls(doc) if doc else None

    @classmethod
    def create(cls, data):
        col = get_snapshots_col()
        doc = {
            'user_id': str(data.get('user_id')),
            'platform': data.get('platform'),
            'snapshot_date': data.get('snapshot_date') or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'rating': int(data.get('rating', 0) or 0),
            'rating_delta': int(data.get('rating_delta', 0) or 0),
            'rank': data.get('rank', 'Unrated'),
            'global_rank': int(data.get('global_rank', 0) or 0),
            'country_rank': int(data.get('country_rank', 0) or 0),
            'problems_solved': int(data.get('problems_solved', 0) or 0),
            'problems_solved_delta': int(data.get('problems_solved_delta', 0) or 0),
            'contests': int(data.get('contests', 0) or 0),
            'contests_delta': int(data.get('contests_delta', 0) or 0),
            'calculated_score': float(data.get('calculated_score', 0.0) or 0.0),
            'created_at': data.get('created_at', utc_now())
        }
        res = col.find_one_and_update(
            {'user_id': doc['user_id'], 'platform': doc['platform'], 'snapshot_date': doc['snapshot_date']},
            {'$set': doc},
            upsert=True,
            return_document=True
        )
        return cls(res or doc)

# =============================================================================
# REMOVAL REQUEST MODEL & REPOSITORY
# =============================================================================
class RemovalRequest:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.id = str(self._id) if self._id is not None else None
        self.staff_id = str(doc.get('staff_id', ''))
        self.student_id = str(doc.get('student_id', ''))
        self.classroom_id = str(doc.get('classroom_id', ''))
        self.reason = doc.get('reason', '')
        self.status = doc.get('status', 'pending')  # 'pending', 'approved', 'rejected'
        self.reviewed_by_id = str(doc.get('reviewed_by_id', '')) if doc.get('reviewed_by_id') else None
        self.reviewed_at = doc.get('reviewed_at')
        self.admin_notes = doc.get('admin_notes')
        self.created_at = doc.get('created_at', utc_now())
        self.updated_at = doc.get('updated_at', utc_now())

    @property
    def staff_member(self):
        return User.find_by_id(self.staff_id)

    @property
    def student_member(self):
        return User.find_by_id(self.student_id)

    @property
    def classroom(self):
        return Classroom.find_by_id(self.classroom_id)

    @property
    def reviewer(self):
        return User.find_by_id(self.reviewed_by_id) if self.reviewed_by_id else None

    @classmethod
    def find_by_id(cls, request_id):
        if not request_id:
            return None
        doc = get_removal_requests_col().find_one({'_id': to_object_id(request_id)})
        return cls(doc) if doc else None

    @classmethod
    def find_by_classroom_id(cls, classroom_id, status=None):
        q = {'classroom_id': str(classroom_id)}
        if status:
            q['status'] = status
        return [cls(d) for d in get_removal_requests_col().find(q).sort('created_at', DESCENDING)]

    @classmethod
    def find_all(cls, status=None):
        q = {}
        if status:
            q['status'] = status
        return [cls(d) for d in get_removal_requests_col().find(q).sort('created_at', DESCENDING)]

    @classmethod
    def create(cls, staff_id, student_id, classroom_id, reason):
        doc = {
            'staff_id': str(staff_id),
            'student_id': str(student_id),
            'classroom_id': str(classroom_id),
            'reason': reason.strip(),
            'status': 'pending',
            'reviewed_by_id': None,
            'reviewed_at': None,
            'admin_notes': None,
            'created_at': utc_now(),
            'updated_at': utc_now()
        }
        res = get_removal_requests_col().insert_one(doc)
        doc['_id'] = res.inserted_id
        return cls(doc)

    def review(self, admin_id, status, admin_notes=""):
        self.status = status
        self.reviewed_by_id = str(admin_id)
        self.reviewed_at = utc_now()
        self.admin_notes = admin_notes.strip() if admin_notes else ""
        self.updated_at = utc_now()
        get_removal_requests_col().update_one(
            {'_id': self._id},
            {'$set': {
                'status': self.status,
                'reviewed_by_id': self.reviewed_by_id,
                'reviewed_at': self.reviewed_at,
                'admin_notes': self.admin_notes,
                'updated_at': self.updated_at
            }}
        )

# =============================================================================
# AUDIT LOG MODEL & REPOSITORY
# =============================================================================
class AuditLog:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.id = str(self._id) if self._id is not None else None
        self.actor_id = str(doc.get('actor_id', '')) if doc.get('actor_id') else None
        self.actor_role = doc.get('actor_role')
        self.event_type = doc.get('event_type', '')
        self.description = doc.get('description', '')
        self.target_type = doc.get('target_type')
        self.target_id = str(doc.get('target_id', '')) if doc.get('target_id') else None
        self.result = doc.get('result', 'success')
        self.ip_address = doc.get('ip_address')
        self.created_at = doc.get('created_at', utc_now())

    @property
    def actor(self):
        return User.find_by_id(self.actor_id) if self.actor_id else None

    @classmethod
    def log(cls, event_type, description, actor_id=None, actor_role=None, target_type=None, target_id=None, result='success', ip_address=None):
        try:
            doc = {
                'actor_id': str(actor_id) if actor_id else None,
                'actor_role': actor_role,
                'event_type': event_type,
                'description': description,
                'target_type': target_type,
                'target_id': str(target_id) if target_id else None,
                'result': result,
                'ip_address': ip_address,
                'created_at': utc_now()
            }
            res = get_audit_logs_col().insert_one(doc)
            doc['_id'] = res.inserted_id
            return cls(doc)
        except Exception as e:
            print(f"Audit log insertion error: {e}")
            return None

    @classmethod
    def find_all(cls, limit=100):
        return [cls(d) for d in get_audit_logs_col().find().sort('created_at', DESCENDING).limit(limit)]

# =============================================================================
# PASSWORD RESET MODEL & REPOSITORY
# =============================================================================
class PasswordReset:
    @staticmethod
    def create_token(user_id, expires_in_minutes=15):
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        expires_at = utc_now() + timedelta(minutes=expires_in_minutes)
        doc = {
            'token_hash': token_hash,
            'user_id': str(user_id),
            'created_at': utc_now(),
            'expires_at': expires_at,
            'used_at': None
        }
        get_password_resets_col().insert_one(doc)
        return raw_token

    @staticmethod
    def verify_token(raw_token):
        if not raw_token:
            return None, "Invalid reset token."
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        doc = get_password_resets_col().find_one({'token_hash': token_hash})
        if not doc:
            return None, "Invalid or expired password reset link."
        if doc.get('used_at'):
            return None, "This password reset link has already been used."
        expires_at = doc.get('expires_at')
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if utc_now() > expires_at:
            return None, "Your password reset link has expired. Please request a new one."
        user = User.find_by_id(doc.get('user_id'))
        if not user or not user.is_active:
            return None, "Associated account was not found or is deactivated."
        return user, None

    @staticmethod
    def mark_used(raw_token):
        if not raw_token:
            return
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        get_password_resets_col().update_one(
            {'token_hash': token_hash},
            {'$set': {'used_at': utc_now()}}
        )

# =============================================================================
# BULK IMPORT STAGING MODEL & REPOSITORY
# =============================================================================
class BulkImport:
    def __init__(self, doc):
        self._doc = doc or {}
        self._id = doc.get('_id')
        self.import_id = doc.get('import_id')
        self.staff_id = str(doc.get('staff_id', ''))
        self.classroom_id = str(doc.get('classroom_id', ''))
        self.filename = doc.get('filename', '')
        self.file_type = doc.get('file_type', '')
        self.status = doc.get('status', 'preview')  # preview, confirmed, processing, completed, failed
        self.rows = doc.get('rows', [])
        self.summary = doc.get('summary', {})
        self.created_at = doc.get('created_at', utc_now())
        self.expires_at = doc.get('expires_at')
        self.confirmed_at = doc.get('confirmed_at')
        self.completed_at = doc.get('completed_at')

    @classmethod
    def create_staging(cls, staff_id, classroom_id, filename, file_type, rows, summary, expires_in_minutes=120):
        import_id = secrets.token_urlsafe(16)
        doc = {
            'import_id': import_id,
            'staff_id': str(staff_id),
            'classroom_id': str(classroom_id),
            'filename': filename,
            'file_type': file_type,
            'status': 'preview',
            'rows': rows,
            'summary': summary,
            'created_at': utc_now(),
            'expires_at': utc_now() + timedelta(minutes=expires_in_minutes),
            'confirmed_at': None,
            'completed_at': None
        }
        res = get_bulk_imports_col().insert_one(doc)
        doc['_id'] = res.inserted_id
        return cls(doc)

    @classmethod
    def find_by_import_id(cls, import_id):
        if not import_id:
            return None
        doc = get_bulk_imports_col().find_one({'import_id': import_id})
        return cls(doc) if doc else None

    @classmethod
    def update_status(cls, import_id, status, additional_fields=None):
        data = {'status': status, 'updated_at': utc_now()}
        if additional_fields:
            data.update(additional_fields)
        get_bulk_imports_col().update_one({'import_id': import_id}, {'$set': data})
