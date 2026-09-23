from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, date
import secrets
from encryption_utils import EncryptedString

db = SQLAlchemy()

def utc_now():
    return datetime.now(timezone.utc)

class User(db.Model):
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='student', nullable=False, index=True)  # 'admin', 'staff', 'student'
    full_name = db.Column(EncryptedString(255), nullable=True)
    student_identifier = db.Column(EncryptedString(255), nullable=True, index=True)  # Roll number, student ID
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)  # Soft delete / activation
    is_email_verified = db.Column(db.Boolean, default=True, nullable=False, index=True)  # Email verified status
    otp_code = db.Column(EncryptedString(255), nullable=True)  # Active OTP
    otp_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    otp_attempts = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships
    created_classrooms = db.relationship('Classroom', backref='staff', foreign_keys='Classroom.staff_id', cascade='all, delete-orphan', lazy='dynamic')
    classroom_memberships = db.relationship('ClassroomMembership', backref='student', foreign_keys='ClassroomMembership.student_id', cascade='all, delete-orphan', lazy='dynamic')
    platform_profiles = db.relationship('PlatformProfile', backref='user', cascade='all, delete-orphan', lazy='dynamic')
    snapshots = db.relationship('PerformanceSnapshot', backref='user', cascade='all, delete-orphan', lazy='dynamic')
    audit_logs = db.relationship('AuditLog', backref='actor', foreign_keys='AuditLog.actor_id', lazy='dynamic')

    def generate_otp(self, expires_in_minutes=10):
        """Generate a cryptographically secure 6-digit numeric OTP."""
        from datetime import timedelta
        code = f"{secrets.randbelow(900000) + 100000}"
        self.otp_code = code
        self.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
        self.otp_attempts = 0
        return code

    def verify_otp(self, candidate_code):
        """Verify the provided OTP against the stored code."""
        if not self.otp_code or not self.otp_expires_at:
            return False, "No active OTP found. Please request a new verification code."
        
        expires_at = self.otp_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        if now > expires_at:
            self.otp_code = None
            self.otp_expires_at = None
            return False, "Your OTP has expired. Please request a new code."

        if self.otp_attempts >= 5:
            self.otp_code = None
            self.otp_expires_at = None
            return False, "Maximum verification attempts exceeded. Please request a new OTP."

        self.otp_attempts += 1
        if candidate_code.strip() == self.otp_code.strip():
            self.is_email_verified = True
            self.otp_code = None
            self.otp_expires_at = None
            self.otp_attempts = 0
            return True, "Email verified successfully!"
        
        return False, "Invalid verification code. Please check and try again."

    def to_dict(self, include_private=False):
        data = {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name or self.username,
            "role": self.role,
            "is_active": self.is_active,
            "is_email_verified": self.is_email_verified,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }
        if include_private:
            data.update({
                "email": self.email,
                "student_identifier": self.student_identifier,
                "last_login_at": self.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_login_at else None
            })
        return data


class Classroom(db.Model):
    __tablename__ = 'classroom'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    invite_token = db.Column(db.String(64), unique=True, nullable=True, index=True)
    invite_token_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)  # 'active', 'archived'
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    memberships = db.relationship('ClassroomMembership', backref='classroom', cascade='all, delete-orphan', lazy='dynamic')
    removal_requests = db.relationship('RemovalRequest', backref='classroom', cascade='all, delete-orphan', lazy='dynamic')

    def generate_invite_token(self):
        """Generates a secure, cryptographically random invitation token."""
        self.invite_token = secrets.token_urlsafe(32)
        return self.invite_token

    def revoke_invite_token(self):
        self.invite_token = None
        self.invite_token_expires_at = None

    def active_students_count(self):
        return self.memberships.filter_by(status='active').count()


class ClassroomMembership(db.Model):
    __tablename__ = 'classroom_membership'
    
    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey('classroom.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)  # 'active', 'removed'
    joined_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('classroom_id', 'student_id', name='uq_classroom_student'),
    )


class PlatformProfile(db.Model):
    __tablename__ = 'platform_profile'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    platform = db.Column(db.String(50), nullable=False, index=True)  # Codeforces, LeetCode, CodeChef, AtCoder, HackerRank
    handle = db.Column(db.String(100), nullable=False, index=True)
    profile_url = db.Column(db.String(255), nullable=False)
    rating = db.Column(db.Integer, default=0, nullable=False)
    rank = db.Column(db.String(60), default='Unrated', nullable=False)
    global_rank = db.Column(db.Integer, default=0, nullable=False)
    country_rank = db.Column(db.Integer, default=0, nullable=False)
    recent_problems = db.Column(db.Integer, default=0, nullable=False)
    total_contests = db.Column(db.Integer, default=0, nullable=False)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    sync_status = db.Column(db.String(20), default='pending', nullable=False)  # 'success', 'failed', 'pending'
    sync_error = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'platform', name='uq_user_platform'),
    )

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


class PerformanceSnapshot(db.Model):
    __tablename__ = 'performance_snapshot'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    platform = db.Column(db.String(50), nullable=False, index=True)
    snapshot_date = db.Column(db.Date, default=lambda: datetime.now(timezone.utc).date(), nullable=False, index=True)
    rating = db.Column(db.Integer, default=0, nullable=False)
    rating_delta = db.Column(db.Integer, default=0, nullable=False)
    rank = db.Column(db.String(60), default='Unrated')
    global_rank = db.Column(db.Integer, default=0)
    country_rank = db.Column(db.Integer, default=0)
    problems_solved = db.Column(db.Integer, default=0, nullable=False)
    problems_solved_delta = db.Column(db.Integer, default=0, nullable=False)
    contests = db.Column(db.Integer, default=0, nullable=False)
    contests_delta = db.Column(db.Integer, default=0, nullable=False)
    calculated_score = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'platform', 'snapshot_date', name='uq_snapshot_user_platform_date'),
    )


class RemovalRequest(db.Model):
    __tablename__ = 'removal_request'
    
    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey('classroom.id'), nullable=False, index=True)
    reason = db.Column(EncryptedString(500), nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)  # 'pending', 'approved', 'rejected'
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    admin_notes = db.Column(EncryptedString(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    # Specific relationship mappings
    staff_member = db.relationship('User', foreign_keys=[staff_id], backref='removal_requests_created')
    student_member = db.relationship('User', foreign_keys=[student_id], backref='removal_requests_targeted')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by_id], backref='removal_requests_reviewed')


class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    event_type = db.Column(db.String(60), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    ip_address = db.Column(db.String(60), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    @classmethod
    def log(cls, event_type, description, actor_id=None, target_type=None, target_id=None, ip_address=None):
        """Creates an audit log record safely."""
        try:
            entry = cls(
                actor_id=actor_id,
                event_type=event_type,
                description=description,
                target_type=target_type,
                target_id=target_id,
                ip_address=ip_address
            )
            db.session.add(entry)
            db.session.commit()
            return entry
        except Exception as e:
            db.session.rollback()
            print(f"Audit log failure: {e}")
            return None
