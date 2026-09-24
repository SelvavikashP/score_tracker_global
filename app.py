import os
import io
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
from flask_apscheduler import APScheduler

from config import Config
from mongo_db import init_indexes, bootstrap_admin, ping_mongodb, get_snapshots_col, to_object_id
from models_mongo import (
    User, Classroom, ClassroomMembership, PlatformProfile,
    PerformanceSnapshot, RemovalRequest, AuditLog, PasswordReset,
    BulkImport, normalize_email
)
from auth_decorators import (
    login_required, role_required, get_current_user,
    verify_classroom_ownership, verify_classroom_access, verify_student_access
)
from api_utils import extract_handle_and_canonical_url
from sync_service import (
    sync_single_platform_profile, sync_student_profiles,
    sync_classroom_profiles, sync_all_portal_profiles
)
from scoring import compute_profile_score
from excel_utils import generate_classroom_excel, generate_student_excel, generate_user_directory_excel
from bulk_import_service import parse_bulk_file, execute_bulk_enrollment, generate_bulk_result_excel
from email_service import (
    send_welcome_email, send_classroom_enrolled_email,
    send_student_joined_staff_notification_email, send_removal_request_submitted_email,
    send_removal_request_decision_email, send_password_reset_token_email,
    send_password_reset_notification_email, send_account_status_notification_email,
    is_smtp_configured
)

app = Flask(__name__)
app.config.from_object(Config)

scheduler = APScheduler()

# Initialize MongoDB Atlas Indexes and Admin Bootstrap
with app.app_context():
    try:
        init_indexes()
        bootstrap_admin()
    except Exception as e:
        print(f"[!] Warning during MongoDB initialization: {e}")

@app.before_request
def load_user_context():
    # 1-Hour Session Inactivity Timeout Enforcement
    if session.get('user_id'):
        last_active = session.get('last_activity')
        now_ts = datetime.now(timezone.utc).timestamp()
        timeout_seconds = getattr(Config, 'SESSION_INACTIVITY_TIMEOUT_SECONDS', 3600)  # 1 hour
        
        if last_active and (now_ts - last_active > timeout_seconds):
            session.clear()
            g.current_user = None
            flash('Your session has expired due to 1 hour of inactivity. Please sign in again.', 'info')
            return redirect(url_for('login'))
        
        # Refresh last activity timestamp and keep session permanent
        session['last_activity'] = now_ts
        session.permanent = True

    g.current_user = get_current_user()
    
    # Enforce mandatory first-login password change for students
    if g.current_user and g.current_user.role == 'student' and g.current_user.must_change_password:
        allowed_endpoints = ['change_password', 'logout', 'static']
        if request.endpoint and request.endpoint not in allowed_endpoints:
            flash('Action Required: You must set your personal password before accessing the student portal.', 'warning')
            return redirect(url_for('change_password'))

# ---------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------
@app.errorhandler(400)
def bad_request_error(e):
    return render_template('errors/error.html', error_code=400, error_title="Bad Request", error_message="The request could not be processed due to invalid syntax."), 400

@app.errorhandler(403)
def forbidden_error(e):
    return render_template('errors/error.html', error_code=403, error_title="Access Forbidden", error_message="You do not have permission to access this resource."), 403

@app.errorhandler(404)
def not_found_error(e):
    return render_template('errors/error.html', error_code=404, error_title="Page Not Found", error_message="The requested resource or page was not found."), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('errors/error.html', error_code=500, error_title="Server Error", error_message="An unexpected server error occurred. Our team has been notified."), 500

@app.route('/health')
def health():
    db_ok, db_status = ping_mongodb()
    return jsonify({
        "status": "healthy" if db_ok else "degraded",
        "database": "mongodb",
        "database_status": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), (200 if db_ok else 503)

# ---------------------------------------------------------
# Public & Global Leaderboard Routes
# ---------------------------------------------------------
@app.route('/')
def index():
    user = get_current_user()
    if user:
        if user.role == 'student':
            return redirect(url_for('student_dashboard'))
        elif user.role == 'staff':
            return redirect(url_for('staff_dashboard'))
        elif user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/classroom/<classroom_id>')
def classroom_shortcut(classroom_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login', next=request.path))
    if user.role == 'student':
        return redirect(url_for('student_classroom_leaderboard', classroom_id=classroom_id))
    elif user.role in ('staff', 'admin'):
        return redirect(url_for('staff_classroom_detail', classroom_id=classroom_id))
    return redirect(url_for('index'))

@app.route('/classroom/join/<path:token>')
def classroom_join_legacy(token):
    user = get_current_user()
    flash('Classroom invitation links have been replaced with direct instructor enrollment. Check your dashboard for active classrooms.', 'info')
    if user:
        if user.role == 'student':
            return redirect(url_for('student_dashboard'))
        elif user.role == 'staff':
            return redirect(url_for('staff_dashboard'))
    return redirect(url_for('login'))

@app.route('/leaderboard')
def public_leaderboard():
    students = User.find_all({'role': 'student', 'is_active': True})
    leaderboard_data = []

    for s in students:
        profiles = PlatformProfile.find_by_user_id(s.id)
        peak_rating = max([p.rating for p in profiles], default=0)
        total_solved = sum([p.recent_problems for p in profiles])
        total_contests = sum([p.total_contests for p in profiles])
        total_score = sum([compute_profile_score(p) for p in profiles])
        platform_slugs = " ".join(p.platform.lower() for p in profiles)

        leaderboard_data.append({
            "student": s,
            "profiles": profiles,
            "peak_rating": peak_rating,
            "total_solved": total_solved,
            "total_contests": total_contests,
            "total_score": total_score,
            "platform_slugs": platform_slugs
        })

    leaderboard_data.sort(key=lambda x: (x['total_score'], x['peak_rating'], x['total_solved']), reverse=True)
    return render_template('index.html', leaderboard=leaderboard_data)

# ---------------------------------------------------------
# Authentication Routes (Role-Separated & Universal)
# ---------------------------------------------------------
def handle_role_login(target_role=None):
    if get_current_user():
        return redirect(url_for('index'))

    role = target_role or request.args.get('role') or 'student'
    if role not in ('student', 'staff', 'admin'):
        role = 'student'

    if request.method == 'POST':
        email_raw = (request.form.get('email') or '').strip()
        norm_email = normalize_email(email_raw)
        password = (request.form.get('password') or '').strip()
        form_role = (request.form.get('target_role') or role).strip().lower()

        user = User.find_by_email(norm_email)
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated. Please contact the portal administrator.', 'danger')
                return redirect(url_for('login', role=form_role))

            # Role verification check
            if form_role and user.role != form_role:
                if form_role == 'admin' and user.role != 'admin':
                    flash(f'Access denied: Account "@{user.username}" does not have Administrator privileges.', 'danger')
                    return redirect(url_for('login_admin'))
                elif form_role == 'staff' and user.role != 'staff' and user.role != 'admin':
                    flash(f'Access denied: Account "@{user.username}" is not registered as Faculty/Staff.', 'danger')
                    return redirect(url_for('login_staff'))

            # Re-initialize session to prevent session fixation
            session.clear()
            session.permanent = True
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role

            user.update_last_login()

            AuditLog.log(
                event_type='LOGIN_SUCCESS',
                description=f'User {user.email} ({user.role}) logged in successfully.',
                actor_id=user.id,
                actor_role=user.role,
                ip_address=request.remote_addr
            )

            # Check if student must change initial temporary password
            if user.role == 'student' and user.must_change_password:
                flash('Action Required: Please set a new personal password before proceeding.', 'warning')
                return redirect(url_for('change_password'))

            flash(f'Welcome back, {user.full_name or user.username}!', 'success')
            next_url = request.args.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)

            if user.role == 'student':
                return redirect(url_for('student_dashboard'))
            elif user.role == 'staff':
                return redirect(url_for('staff_dashboard'))
            elif user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
        else:
            AuditLog.log(
                event_type='LOGIN_FAILURE',
                description=f'Failed login attempt for email: {email_raw}',
                ip_address=request.remote_addr,
                result='failure'
            )
            flash('Invalid email or password. Please check your credentials.', 'danger')

    return render_template('login.html', active_role=role)

@app.route('/login', methods=['GET', 'POST'])
def login():
    return handle_role_login(target_role=request.args.get('role'))

@app.route('/login/student', methods=['GET', 'POST'])
def login_student():
    return handle_role_login(target_role='student')

@app.route('/login/staff', methods=['GET', 'POST'])
def login_staff():
    return handle_role_login(target_role='staff')

@app.route('/login/admin', methods=['GET', 'POST'])
def login_admin():
    return handle_role_login(target_role='admin')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """
    Public student registration is disabled in enterprise configuration.
    Student accounts are provisioned directly by faculty/administrators.
    """
    if get_current_user():
        return redirect(url_for('index'))

    flash('Public student self-registration is disabled. Student accounts are provisioned directly by course instructors or administrators.', 'info')
    return redirect(url_for('login_student'))

@app.route('/logout')
def logout():
    user = get_current_user()
    if user:
        AuditLog.log(
            event_type='LOGOUT',
            description=f'User {user.username} logged out.',
            actor_id=user.id,
            actor_role=user.role,
            ip_address=request.remote_addr
        )
    session.clear()
    flash('You have been signed out successfully.', 'info')
    return redirect(url_for('login'))

# ---------------------------------------------------------
# Password Management Routes (Change, Forgot, Reset)
# ---------------------------------------------------------
@app.route('/student/change-password', methods=['GET', 'POST'], endpoint='change_password')
@login_required
def change_password():
    user = get_current_user()
    if request.method == 'POST':
        current_password = (request.form.get('current_password') or '').strip()
        new_password = (request.form.get('new_password') or '').strip()
        confirm_password = (request.form.get('confirm_password') or '').strip()

        if not user.check_password(current_password):
            flash('Incorrect current password.', 'danger')
            return render_template('student/change_password.html')

        if len(new_password) < 6:
            flash('New password must be at least 6 characters long.', 'warning')
            return render_template('student/change_password.html')

        if new_password == Config.DEFAULT_STUDENT_PASSWORD:
            flash('You cannot keep the default temporary password (Student@123). Please choose a private personal password.', 'warning')
            return render_template('student/change_password.html')

        if new_password != confirm_password:
            flash('New password and confirmation do not match.', 'danger')
            return render_template('student/change_password.html')

        user.set_password(new_password)

        AuditLog.log(
            event_type='PASSWORD_CHANGED',
            description=f'User {user.email} successfully updated their password.',
            actor_id=user.id,
            actor_role=user.role,
            ip_address=request.remote_addr
        )

        try:
            send_password_reset_notification_email(user.email, user.full_name or user.username)
        except Exception as e:
            app.logger.warning(f"Could not send password update confirmation email: {e}")

        flash('Your password has been updated successfully! Welcome to your dashboard.', 'success')
        return redirect(url_for('student_dashboard' if user.role == 'student' else 'index'))

    return render_template('student/change_password.html')

@app.route('/student/forgot-password', methods=['GET', 'POST'], endpoint='forgot_password')
def forgot_password():
    if get_current_user():
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = normalize_email(request.form.get('email'))
        if email:
            user = User.find_by_email(email)
            if user and user.is_active:
                raw_token = PasswordReset.create_token(user.id, expires_in_minutes=Config.PASSWORD_RESET_EXPIRE_MINUTES)
                reset_url = f"{Config.APP_BASE_URL.rstrip('/')}/student/reset-password/{raw_token}"
                
                AuditLog.log(
                    event_type='PASSWORD_RESET_REQUESTED',
                    description=f'Password reset link requested for user {user.email}',
                    actor_id=user.id,
                    actor_role=user.role,
                    ip_address=request.remote_addr
                )
                
                try:
                    send_password_reset_token_email(user.email, user.full_name or user.username, reset_url)
                except Exception as e:
                    app.logger.warning(f"Failed to dispatch password reset email: {e}")

        # Always display safe generic message to avoid user enumeration
        flash('If an active account is registered with that email address, password reset instructions have been sent.', 'info')
        return redirect(url_for('login_student'))

    return render_template('student/forgot_password.html')

@app.route('/student/reset-password/<token>', methods=['GET', 'POST'], endpoint='reset_password')
def reset_password(token):
    if get_current_user():
        return redirect(url_for('index'))

    user, error_msg = PasswordReset.verify_token(token)
    if error_msg or not user:
        flash(error_msg or 'Invalid or expired password reset link.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_password = (request.form.get('new_password') or '').strip()
        confirm_password = (request.form.get('confirm_password') or '').strip()

        if len(new_password) < 6:
            flash('Password must be at least 6 characters long.', 'warning')
            return render_template('student/reset_password.html', token=token, user=user)

        if new_password == Config.DEFAULT_STUDENT_PASSWORD:
            flash('You cannot use the default temporary password (Student@123). Please choose a private personal password.', 'warning')
            return render_template('student/reset_password.html', token=token, user=user)

        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('student/reset_password.html', token=token, user=user)

        user.set_password(new_password)
        PasswordReset.mark_used(token)

        AuditLog.log(
            event_type='PASSWORD_RESET_COMPLETED',
            description=f'User {user.email} completed password reset using token.',
            actor_id=user.id,
            actor_role=user.role,
            ip_address=request.remote_addr
        )

        try:
            send_password_reset_notification_email(user.email, user.full_name or user.username)
        except Exception as e:
            app.logger.warning(f"Could not send password reset confirmation email: {e}")

        flash('Your password has been reset successfully! Please sign in with your new password.', 'success')
        return redirect(url_for('login_student'))

    return render_template('student/reset_password.html', token=token, user=user)

# ---------------------------------------------------------
# Student Portal Routes
# ---------------------------------------------------------
@app.route('/student/dashboard')
@login_required
@role_required('student')
def student_dashboard():
    student = get_current_user()
    profiles = PlatformProfile.find_by_user_id(student.id)
    classrooms = student.get_classrooms()

    top_rating = max([p.rating for p in profiles], default=0)
    total_solved = sum([p.recent_problems for p in profiles])
    total_contests = sum([p.total_contests for p in profiles])

    stats = {
        "top_rating": top_rating,
        "total_solved": total_solved,
        "total_contests": total_contests,
        "total_profiles": len(profiles)
    }

    snapshots = PerformanceSnapshot.find_by_user_id(student.id, limit=30)
    # Sort chronological for charts
    snapshots.sort(key=lambda s: str(s.snapshot_date))

    date_map = {}
    for s in snapshots:
        d_str = str(s.snapshot_date)[:10]
        if d_str not in date_map:
            date_map[d_str] = {"rating": 0, "solved": 0}
        date_map[d_str]["rating"] = max(date_map[d_str]["rating"], s.rating)
        date_map[d_str]["solved"] += s.problems_solved

    chart_dates = list(date_map.keys())
    chart_ratings = [date_map[d]["rating"] for d in chart_dates]
    chart_solved = [date_map[d]["solved"] for d in chart_dates]

    return render_template(
        'student/dashboard.html',
        student=student,
        profiles=profiles,
        classrooms=classrooms,
        stats=stats,
        chart_dates=chart_dates,
        chart_ratings=chart_ratings,
        chart_solved=chart_solved
    )

@app.route('/student/profile/add', methods=['POST'])
@login_required
@role_required('student')
def student_add_profile():
    student = get_current_user()
    platform = (request.form.get('platform') or '').strip()
    profile_input = (request.form.get('profile_url') or '').strip()

    if not platform or not profile_input:
        flash('Platform and handle/URL are required.', 'danger')
        return redirect(url_for('student_dashboard'))

    handle, canonical_url = extract_handle_and_canonical_url(profile_input, platform)
    if not handle or not canonical_url:
        flash(f'Invalid {platform} profile URL or handle format.', 'danger')
        return redirect(url_for('student_dashboard'))

    existing = PlatformProfile.find_one(student.id, platform)
    if existing:
        flash(f'You already have a {platform} handle ({existing.handle}) connected. Disconnect it first to add a new handle.', 'warning')
        return redirect(url_for('student_dashboard'))

    new_profile = PlatformProfile.upsert(student.id, platform, {
        'handle': handle,
        'profile_url': canonical_url,
        'sync_status': 'pending'
    })

    res = sync_single_platform_profile(new_profile, force=True)
    if res.get("success"):
        flash(f'Successfully connected and synchronized {platform} handle "{handle}"!', 'success')
    else:
        flash(f'Connected {platform} handle "{handle}", but initial sync failed: {res.get("error")}', 'warning')

    return redirect(url_for('student_dashboard'))

@app.route('/student/profile/sync/<profile_id>')
@login_required
@role_required('student')
def student_sync_profile(profile_id):
    student = get_current_user()
    profile = PlatformProfile.find_by_id(profile_id)
    if not profile or str(profile.user_id) != str(student.id):
        abort(404)

    res = sync_single_platform_profile(profile, force=True)
    if res.get("success"):
        flash(f'Updated stats for {profile.platform} ({profile.handle})!', 'success')
    else:
        flash(f'Failed to refresh {profile.platform}: {res.get("error")}', 'warning')
    return redirect(url_for('student_dashboard'))

@app.route('/student/profile/delete/<profile_id>')
@login_required
@role_required('student')
def student_delete_profile(profile_id):
    student = get_current_user()
    profile = PlatformProfile.find_by_id(profile_id)
    if not profile or str(profile.user_id) != str(student.id):
        abort(404)

    plat = profile.platform
    h = profile.handle
    PlatformProfile.delete_by_id(profile_id)
    flash(f'Disconnected {plat} handle "{h}".', 'info')
    return redirect(url_for('student_dashboard'))

@app.route('/student/sync-all')
@login_required
@role_required('student')
def student_sync_profiles():
    student = get_current_user()
    res = sync_student_profiles(student.id, force=True)
    flash(f'Synchronized {res.get("synced", 0)} of {res.get("total", 0)} platform profiles.', 'success')
    return redirect(url_for('student_dashboard'))

@app.route('/student/classroom/<classroom_id>')
@login_required
def student_classroom_leaderboard(classroom_id):
    user = get_current_user()
    if user.role in ('staff', 'admin'):
        return redirect(url_for('staff_classroom_detail', classroom_id=classroom_id))

    classroom = Classroom.find_by_id(classroom_id)
    if not classroom:
        flash('The requested classroom was not found.', 'warning')
        return redirect(url_for('student_dashboard'))

    membership = ClassroomMembership.find_one(classroom.id, user.id)
    if not membership or membership.status != 'active':
        flash(f'You are not enrolled in classroom "{classroom.name}". Please ask your faculty instructor to enroll your email.', 'warning')
        return redirect(url_for('student_dashboard'))

    memberships = ClassroomMembership.find_by_classroom_id(classroom.id, status='active')
    student_scores = []

    for m in memberships:
        s = m.student
        if not s or not s.is_active:
            continue
        profiles = PlatformProfile.find_by_user_id(s.id)
        peak_rating = max([p.rating for p in profiles], default=0)
        total_solved = sum([p.recent_problems for p in profiles])
        total_contests = sum([p.total_contests for p in profiles])
        total_score = sum([compute_profile_score(p) for p in profiles])

        student_scores.append({
            "student": s,
            "profiles": profiles,
            "peak_rating": peak_rating,
            "total_solved": total_solved,
            "total_contests": total_contests,
            "total_score": total_score,
            "platforms": profiles
        })

    student_scores.sort(key=lambda x: (x['total_score'], x['peak_rating'], x['total_solved']), reverse=True)
    return render_template('student/classroom.html', classroom=classroom, student_scores=student_scores)

# ---------------------------------------------------------
# Staff / Teacher Portal Routes
# ---------------------------------------------------------
@app.route('/staff/dashboard')
@login_required
@role_required('staff', 'admin')
def staff_dashboard():
    staff = get_current_user()
    if staff.role == 'admin':
        classrooms = Classroom.find_all()
    else:
        classrooms = Classroom.find_by_staff_id(staff.id, status='active')

    class_ids = [c.id for c in classrooms]
    all_memberships = []
    for cid in class_ids:
        all_memberships.extend(ClassroomMembership.find_by_classroom_id(cid, status='active'))

    unique_student_ids = list(set(m.student_id for m in all_memberships))
    students_list = [User.find_by_id(sid) for sid in unique_student_ids if User.find_by_id(sid)]

    profiles_list = []
    for sid in unique_student_ids:
        profiles_list.extend(PlatformProfile.find_by_user_id(sid))

    if staff.role == 'admin':
        staff_removal_requests = RemovalRequest.find_all()
    else:
        staff_removal_requests = []
        for cid in class_ids:
            staff_removal_requests.extend(RemovalRequest.find_by_classroom_id(cid))

    pending_removals = [r for r in staff_removal_requests if r.status == 'pending']

    student_classrooms_map = {}
    for m in all_memberships:
        c = Classroom.find_by_id(m.classroom_id)
        if c:
            student_classrooms_map.setdefault(m.student_id, []).append(c.name)

    return render_template(
        'staff/dashboard.html',
        staff=staff,
        classrooms=classrooms,
        total_students=len(unique_student_ids),
        students_list=students_list,
        student_classrooms_map=student_classrooms_map,
        total_profiles=len(profiles_list),
        profiles_list=profiles_list,
        pending_removals_count=len(pending_removals),
        pending_removals_list=pending_removals,
        all_removal_requests=staff_removal_requests
    )

@app.route('/staff/classroom/create', methods=['POST'])
@login_required
@role_required('staff', 'admin')
def staff_classroom_create():
    staff = get_current_user()
    name = (request.form.get('name') or '').strip()
    description = (request.form.get('description') or '').strip()

    if not name:
        flash('Classroom name is required.', 'danger')
        return redirect(url_for('staff_dashboard'))

    new_classroom = Classroom.create(
        name=name,
        staff_id=staff.id,
        description=description
    )

    AuditLog.log(
        event_type='CLASSROOM_CREATED',
        description=f'Staff {staff.username} created classroom "{name}"',
        actor_id=staff.id,
        actor_role=staff.role,
        target_type='Classroom',
        target_id=new_classroom.id,
        ip_address=request.remote_addr
    )
    flash(f'Classroom "{name}" created successfully!', 'success')
    return redirect(url_for('staff_classroom_detail', classroom_id=new_classroom.id))

@app.route('/staff/classroom/<classroom_id>')
@login_required
@role_required('staff', 'admin')
def staff_classroom_detail(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    memberships = ClassroomMembership.find_by_classroom_id(classroom.id, status='active')
    students_data = []

    for m in memberships:
        s = m.student
        if not s:
            continue
        profiles = PlatformProfile.find_by_user_id(s.id)
        peak_rating = max([p.rating for p in profiles], default=0)
        total_solved = sum([p.recent_problems for p in profiles])
        students_data.append({
            "student": s,
            "profiles": profiles,
            "peak_rating": peak_rating,
            "total_solved": total_solved
        })

    return render_template('staff/classroom.html', classroom=classroom, students_data=students_data)

@app.route('/staff/user/create', methods=['POST'], endpoint='staff_create_student_user')
@login_required
@role_required('staff', 'admin')
def staff_create_student_user():
    staff = get_current_user()
    full_name = (request.form.get('full_name') or '').strip()
    username = (request.form.get('username') or '').strip()
    email_raw = (request.form.get('email') or '').strip()
    norm_email = normalize_email(email_raw)
    password = (request.form.get('password') or '').strip() or Config.DEFAULT_STUDENT_PASSWORD
    student_id = (request.form.get('student_identifier') or '').strip()
    classroom_id = request.form.get('classroom_id')

    if not norm_email:
        flash('Email address is required.', 'danger')
        return redirect(request.referrer or url_for('staff_dashboard'))

    existing = User.find_by_email(norm_email)
    if existing:
        if existing.role != 'student':
            flash(f'This email belongs to another account type ({existing.role}).', 'danger')
            return redirect(request.referrer or url_for('staff_dashboard'))
        if classroom_id:
            ClassroomMembership.create_or_activate(classroom_id, existing.id)
            flash(f'Enrolled existing student {existing.full_name or existing.username} into the selected classroom.', 'success')
        else:
            flash(f'Student account {existing.email} already exists.', 'info')
        return redirect(request.referrer or url_for('staff_dashboard'))

    uname_base = username or norm_email.split('@')[0]
    candidate = uname_base
    collision = 1
    while User.find_by_username(candidate):
        collision += 1
        candidate = f"{uname_base}_{collision}"

    new_user = User.create({
        'email': email_raw,
        'username': candidate,
        'full_name': full_name or candidate,
        'student_identifier': student_id or None,
        'password_hash': generate_password_hash(password),
        'role': 'student',
        'is_active': True,
        'is_email_verified': True,
        'must_change_password': True if password == Config.DEFAULT_STUDENT_PASSWORD else False,
        'account_source': 'staff_created'
    })

    if classroom_id:
        ClassroomMembership.create_or_activate(classroom_id, new_user.id)

    AuditLog.log(
        event_type='STUDENT_CREATED',
        description=f'Staff {staff.username} provisioned student account @{new_user.username} ({new_user.email})',
        actor_id=staff.id,
        actor_role=staff.role,
        target_type='User',
        target_id=new_user.id,
        ip_address=request.remote_addr
    )

    try:
        portal_login_url = f"{Config.APP_BASE_URL.rstrip('/')}/login/student"
        send_welcome_email(
            recipient_email=new_user.email,
            recipient_name=new_user.full_name,
            username=new_user.username,
            role='student',
            student_identifier=new_user.student_identifier,
            login_url=portal_login_url,
            is_newly_provisioned=True,
            default_password=password
        )
    except Exception as e:
        app.logger.warning(f"Could not send welcome email: {e}")

    flash(f'Student account for {new_user.full_name} created successfully!', 'success')
    return redirect(request.referrer or url_for('staff_dashboard'))

# ---------------------------------------------------------
# Single Student Provisioning & Enrollment Endpoint
# ---------------------------------------------------------
@app.route('/staff/classroom/<classroom_id>/student/add', methods=['POST'], endpoint='staff_classroom_add_student')
@app.route('/staff/classroom/<classroom_id>/add-student', methods=['POST'], endpoint='staff_classroom_add_student_legacy')
@login_required
@role_required('staff', 'admin')
def staff_classroom_add_student(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    raw_email = (request.form.get('student_email') or request.form.get('student_identifier') or '').strip()
    norm_email = normalize_email(raw_email)
    full_name = (request.form.get('full_name') or '').strip()
    student_identifier = (request.form.get('student_identifier') if request.form.get('full_name') else None)

    if not norm_email or '@' not in norm_email:
        flash('A valid student email address is required.', 'danger')
        return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

    existing_user = User.find_by_email(norm_email)
    if existing_user:
        # Wrong role protection
        if existing_user.role != 'student':
            flash(f'This email belongs to another account type ({existing_user.role}). Cannot enroll as a student.', 'danger')
            return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

        # Enroll existing student without touching password
        mem, created = ClassroomMembership.create_or_activate(classroom.id, existing_user.id)
        if created or mem.status == 'active':
            AuditLog.log(
                event_type='STUDENT_ENROLLED',
                description=f'Staff {staff.username} enrolled existing student {existing_user.email} into "{classroom.name}"',
                actor_id=staff.id,
                actor_role=staff.role,
                target_type='Classroom',
                target_id=classroom.id,
                ip_address=request.remote_addr
            )
            try:
                send_classroom_enrolled_email(
                    student_email=existing_user.email,
                    student_name=existing_user.full_name or existing_user.username,
                    classroom_name=classroom.name,
                    staff_name=staff.full_name or staff.username,
                    is_new_student=False
                )
            except Exception as e:
                app.logger.warning(f"Could not send enrollment email: {e}")

            flash(f'Enrolled existing student {existing_user.full_name or existing_user.username} into {classroom.name}.', 'success')
        else:
            flash(f'{existing_user.full_name or existing_user.username} is already enrolled in {classroom.name}.', 'info')
    else:
        # Provision new student account
        username = norm_email.split('@')[0]
        # Ensure username uniqueness
        if User.find_by_username(username):
            username = f"{username}_{int(datetime.now().timestamp()) % 10000}"

        new_user = User.create({
            'email': raw_email,
            'username': username,
            'full_name': full_name or username,
            'student_identifier': student_identifier,
            'password_hash': generate_password_hash(Config.DEFAULT_STUDENT_PASSWORD),
            'role': 'student',
            'is_active': True,
            'is_email_verified': True,
            'must_change_password': True,
            'account_source': 'staff_created'
        })

        ClassroomMembership.create_or_activate(classroom.id, new_user.id)

        AuditLog.log(
            event_type='STUDENT_CREATED',
            description=f'Staff {staff.username} provisioned new student {new_user.email} with default password',
            actor_id=staff.id,
            actor_role=staff.role,
            target_type='User',
            target_id=new_user.id,
            ip_address=request.remote_addr
        )

        AuditLog.log(
            event_type='STUDENT_ENROLLED',
            description=f'Staff {staff.username} enrolled new student {new_user.email} into "{classroom.name}"',
            actor_id=staff.id,
            actor_role=staff.role,
            target_type='Classroom',
            target_id=classroom.id,
            ip_address=request.remote_addr
        )

        try:
            portal_login_url = f"{Config.APP_BASE_URL.rstrip('/')}/login/student"
            send_welcome_email(
                recipient_email=new_user.email,
                recipient_name=new_user.full_name,
                username=new_user.username,
                role='student',
                student_identifier=new_user.student_identifier,
                login_url=portal_login_url,
                is_newly_provisioned=True,
                default_password=Config.DEFAULT_STUDENT_PASSWORD
            )
        except Exception as e:
            app.logger.warning(f"Could not send welcome email to new student: {e}")

        flash(f'New student account for {new_user.full_name} ({new_user.email}) created with initial password Student@123 and enrolled into {classroom.name}!', 'success')

    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

# ---------------------------------------------------------
# Two-Step Bulk Import Endpoints (.xlsx, .csv, .txt)
# ---------------------------------------------------------
@app.route('/staff/classroom/<classroom_id>/students/bulk-upload', methods=['POST'], endpoint='staff_classroom_bulk_upload_preview')
@login_required
@role_required('staff', 'admin')
def staff_classroom_bulk_upload_preview(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No file selected."}), 400

    parsed_data, err = parse_bulk_file(file, staff.id, classroom.id)
    if err or not parsed_data:
        return jsonify({"success": False, "error": err or "Failed to parse file."}), 400

    AuditLog.log(
        event_type='BULK_IMPORT_STARTED',
        description=f'Staff {staff.username} uploaded bulk file "{file.filename}" for preview ({parsed_data["summary"]["total_rows"]} rows)',
        actor_id=staff.id,
        actor_role=staff.role,
        target_type='Classroom',
        target_id=classroom.id,
        ip_address=request.remote_addr
    )

    return jsonify({
        "success": True,
        "import_id": parsed_data["import_id"],
        "summary": parsed_data["summary"],
        "rows": parsed_data["rows"]
    }), 200

@app.route('/staff/classroom/<classroom_id>/students/bulk-upload/confirm', methods=['POST'], endpoint='staff_classroom_bulk_upload_confirm')
@login_required
@role_required('staff', 'admin')
def staff_classroom_bulk_upload_confirm(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    req_data = request.get_json(silent=True) or request.form
    import_id = req_data.get('import_id')
    if not import_id:
        return jsonify({"success": False, "error": "Missing import_id parameter."}), 400

    staging_doc = BulkImport.find_by_import_id(import_id)
    if not staging_doc:
        return jsonify({"success": False, "error": "Staged import record not found or expired."}), 404

    # Validate staff and classroom ownership of staged record
    if str(staging_doc.staff_id) != str(staff.id) and staff.role != 'admin':
        return jsonify({"success": False, "error": "Unauthorized access to this import batch."}), 403

    if str(staging_doc.classroom_id) != str(classroom.id):
        return jsonify({"success": False, "error": "Classroom ID mismatch for this import batch."}), 400

    if staging_doc.status not in ('preview', 'failed'):
        return jsonify({"success": False, "error": f"Import batch is already {staging_doc.status}."}), 400

    AuditLog.log(
        event_type='BULK_IMPORT_CONFIRMED',
        description=f'Staff {staff.username} confirmed bulk enrollment for import {import_id}',
        actor_id=staff.id,
        actor_role=staff.role,
        target_type='Classroom',
        target_id=classroom.id,
        ip_address=request.remote_addr
    )

    summary_result, excel_bytes = execute_bulk_enrollment(staging_doc, staff, classroom)

    AuditLog.log(
        event_type='BULK_IMPORT_COMPLETED',
        description=f'Staff {staff.username} completed bulk import: {summary_result["new_accounts_created"]} new, {summary_result["existing_enrolled"]} existing enrolled',
        actor_id=staff.id,
        actor_role=staff.role,
        target_type='Classroom',
        target_id=classroom.id,
        ip_address=request.remote_addr
    )

    return jsonify({
        "success": True,
        "import_id": import_id,
        "summary": summary_result
    }), 200

@app.route('/staff/classroom/<classroom_id>/students/bulk-upload/<import_id>/report', endpoint='staff_classroom_bulk_report')
@login_required
@role_required('staff', 'admin')
def staff_classroom_bulk_report(classroom_id, import_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    staging_doc = BulkImport.find_by_import_id(import_id)
    if not staging_doc:
        abort(404)

    if str(staging_doc.classroom_id) != str(classroom.id):
        abort(403)

    excel_bytes = generate_bulk_result_excel(staging_doc, classroom.name)
    return send_file(
        io.BytesIO(excel_bytes),
        as_attachment=True,
        download_name=f"Bulk_Import_Result_{classroom.name}_{import_id[:8]}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/staff/classroom/<classroom_id>/leaderboard')
@login_required
@role_required('staff', 'admin')
def staff_classroom_leaderboard(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    memberships = ClassroomMembership.find_by_classroom_id(classroom.id, status='active')
    student_scores = []

    for m in memberships:
        s = m.student
        if not s or not s.is_active:
            continue
        profiles = PlatformProfile.find_by_user_id(s.id)
        peak_rating = max([p.rating for p in profiles], default=0)
        total_solved = sum([p.recent_problems for p in profiles])
        total_contests = sum([p.total_contests for p in profiles])
        total_score = sum([compute_profile_score(p) for p in profiles])

        student_scores.append({
            "student": s,
            "profiles": profiles,
            "peak_rating": peak_rating,
            "total_solved": total_solved,
            "total_contests": total_contests,
            "total_score": total_score,
            "platforms": profiles
        })

    student_scores.sort(key=lambda x: (x['total_score'], x['peak_rating'], x['total_solved']), reverse=True)
    return render_template('staff/leaderboard.html', classroom=classroom, student_scores=student_scores)

@app.route('/staff/student/<student_id>')
@login_required
@role_required('staff', 'admin')
def staff_student_detail(student_id):
    staff = get_current_user()
    student = verify_student_access(student_id, staff)

    profiles = PlatformProfile.find_by_user_id(student.id)
    snapshots = PerformanceSnapshot.find_by_user_id(student.id, limit=30)
    snapshots.sort(key=lambda s: str(s.snapshot_date))

    date_map = {}
    for s in snapshots:
        d_str = str(s.snapshot_date)[:10]
        if d_str not in date_map:
            date_map[d_str] = {"rating": 0, "solved": 0}
        date_map[d_str]["rating"] = max(date_map[d_str]["rating"], s.rating)
        date_map[d_str]["solved"] += s.problems_solved

    chart_dates = list(date_map.keys())
    chart_ratings = [date_map[d]["rating"] for d in chart_dates]
    chart_solved = [date_map[d]["solved"] for d in chart_dates]

    return render_template(
        'staff/student_detail.html',
        student=student,
        profiles=profiles,
        snapshots=snapshots,
        chart_dates=chart_dates,
        chart_ratings=chart_ratings,
        chart_solved=chart_solved
    )

@app.route('/staff/student/<student_id>/request-removal', methods=['POST'])
@login_required
@role_required('staff')
def staff_request_removal(student_id):
    staff = get_current_user()
    student = verify_student_access(student_id, staff)
    classroom_id = request.form.get('classroom_id')
    reason = (request.form.get('reason') or '').strip()

    classroom = verify_classroom_ownership(classroom_id, staff)
    if not reason:
        flash('Reason for removal is required.', 'danger')
        return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

    req = RemovalRequest.create(
        staff_id=staff.id,
        student_id=student.id,
        classroom_id=classroom.id,
        reason=reason
    )

    AuditLog.log(
        event_type='REMOVAL_REQUEST_SUBMITTED',
        description=f'Staff {staff.username} submitted removal request for student {student.username} from "{classroom.name}"',
        actor_id=staff.id,
        actor_role=staff.role,
        target_type='RemovalRequest',
        target_id=req.id,
        ip_address=request.remote_addr
    )

    try:
        admin_user = User.find_all({'role': 'admin'})
        admin_email = admin_user[0].email if admin_user else Config.INITIAL_ADMIN_EMAIL
        send_removal_request_submitted_email(
            admin_email=admin_email,
            staff_name=staff.full_name or staff.username,
            student_name=student.full_name or student.username,
            classroom_name=classroom.name,
            reason=reason
        )
    except Exception as e:
        app.logger.warning(f"Could not send removal request notification to admin: {e}")

    flash(f'Removal request for {student.full_name or student.username} submitted to Administrator.', 'info')
    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

@app.route('/staff/sync/classroom/<classroom_id>')
@login_required
@role_required('staff', 'admin')
def staff_sync_classroom(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)
    res = sync_classroom_profiles(classroom.id, force=True)
    flash(f'Class sync completed: {res.get("synced_profiles", 0)} of {res.get("total_profiles", 0)} profiles updated.', 'success')
    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

@app.route('/staff/sync/student/<student_id>')
@login_required
@role_required('staff', 'admin')
def staff_sync_student(student_id):
    staff = get_current_user()
    student = verify_student_access(student_id, staff)
    res = sync_student_profiles(student.id, force=True)
    flash(f'Synchronized {res.get("synced", 0)} platform profiles for {student.full_name or student.username}.', 'success')
    return redirect(url_for('staff_student_detail', student_id=student.id))

# ---------------------------------------------------------
# Admin Portal Routes
# ---------------------------------------------------------
@app.route('/admin/dashboard')
@login_required
@role_required('admin')
def admin_dashboard():
    students_list = User.find_all({'role': 'student'})
    staff_list = User.find_all({'role': 'staff'})
    classrooms_list = Classroom.find_all()
    pending_removals_list = RemovalRequest.find_all(status='pending')
    all_removals_list = RemovalRequest.find_all()[:25]
    recent_logs = AuditLog.find_all(limit=8)

    staff_classrooms_count = {}
    for c in classrooms_list:
        staff_classrooms_count[c.staff_id] = staff_classrooms_count.get(c.staff_id, 0) + 1

    return render_template(
        'admin/dashboard.html',
        total_students=len(students_list),
        total_staff=len(staff_list),
        total_classrooms=len(classrooms_list),
        pending_removals_count=len(pending_removals_list),
        students_list=students_list,
        staff_list=staff_list,
        staff_classrooms_count=staff_classrooms_count,
        classrooms_list=classrooms_list,
        pending_removals_list=pending_removals_list,
        all_removals_list=all_removals_list,
        recent_logs=recent_logs
    )

@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    users = User.find_all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/export')
@login_required
@role_required('admin')
def admin_export_users():
    admin = get_current_user()
    users = User.find_all()
    file_path, filename = generate_user_directory_excel(users)

    AuditLog.log(
        event_type='USER_DIRECTORY_EXPORTED',
        description=f'Admin {admin.username} exported user directory ({len(users)} accounts)',
        actor_id=admin.id,
        actor_role=admin.role,
        ip_address=request.remote_addr
    )
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/admin/user/<user_id>/reset-password', methods=['POST'])
@login_required
@role_required('admin')
def admin_reset_user_password(user_id):
    admin = get_current_user()
    target_user = User.find_by_id(user_id)
    if not target_user:
        flash('User account not found.', 'danger')
        return redirect(url_for('admin_users'))

    new_password = (request.form.get('new_password') or '').strip()
    if not new_password or len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'danger')
        return redirect(url_for('admin_users'))

    target_user.set_password(new_password)

    try:
        send_password_reset_notification_email(target_user.email, target_user.full_name or target_user.username)
    except Exception as e:
        app.logger.warning(f"Failed to send password reset notification email: {e}")

    AuditLog.log(
        event_type='ADMIN_PASSWORD_RESET',
        description=f'Admin {admin.username} reset password for user {target_user.username}',
        actor_id=admin.id,
        actor_role=admin.role,
        target_type='User',
        target_id=target_user.id,
        ip_address=request.remote_addr
    )
    flash(f'Password for @{target_user.username} ({target_user.email}) updated successfully.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<user_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_user(user_id):
    admin = get_current_user()
    target_user = User.find_by_id(user_id)
    if not target_user:
        flash('User account not found.', 'danger')
        return redirect(url_for('admin_users'))

    if str(target_user.id) == str(admin.id):
        flash('Cannot delete your own active administrator account.', 'warning')
        return redirect(url_for('admin_users'))

    u_email = target_user.email
    u_role = target_user.role
    User.delete_by_id(user_id)

    AuditLog.log(
        event_type='ACCOUNT_DEACTIVATED',
        description=f'Admin {admin.username} permanently deleted user {u_email} ({u_role})',
        actor_id=admin.id,
        actor_role=admin.role,
        target_type='User',
        target_id=user_id,
        ip_address=request.remote_addr
    )
    flash(f'User {u_email} has been permanently removed.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<user_id>/toggle-status', methods=['POST'])
@login_required
@role_required('admin')
def admin_toggle_user_status(user_id):
    admin = get_current_user()
    target_user = User.find_by_id(user_id)
    if not target_user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_users'))

    if str(target_user.id) == str(admin.id):
        flash('Cannot deactivate your own active admin account.', 'danger')
        return redirect(url_for('admin_users'))

    new_status = not target_user.is_active
    User.update_by_id(user_id, {'is_active': new_status})

    try:
        send_account_status_notification_email(target_user.email, target_user.full_name or target_user.username, is_active=new_status)
    except Exception as e:
        app.logger.warning(f"Failed to send account status notification: {e}")

    status_str = "activated" if new_status else "deactivated"
    AuditLog.log(
        event_type='ACCOUNT_REACTIVATED' if new_status else 'ACCOUNT_DEACTIVATED',
        description=f'Admin {admin.username} {status_str} user {target_user.email}',
        actor_id=admin.id,
        actor_role=admin.role,
        target_type='User',
        target_id=target_user.id,
        ip_address=request.remote_addr
    )
    flash(f'User {target_user.username} has been {status_str}.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/user/create', methods=['POST'], endpoint='admin_create_user')
@app.route('/admin/staff/create', methods=['POST'], endpoint='admin_create_staff')
@login_required
@role_required('admin')
def admin_create_user():
    admin = get_current_user()
    full_name = (request.form.get('full_name') or '').strip()
    username = (request.form.get('username') or '').strip()
    email_raw = (request.form.get('email') or '').strip()
    norm_email = normalize_email(email_raw)
    password = (request.form.get('password') or '').strip()
    role = (request.form.get('role') or 'staff').strip().lower()
    student_id = (request.form.get('student_identifier') or '').strip()

    if role not in ['staff', 'student', 'admin']:
        role = 'staff'

    if not username or not norm_email or not password:
        flash('Full name, username, email, and password are required.', 'danger')
        return redirect(url_for('admin_users'))

    if len(password) < 6:
        flash('Password must be at least 6 characters long.', 'warning')
        return redirect(url_for('admin_users'))

    if User.find_by_email(norm_email):
        flash('An account with this email already exists.', 'warning')
        return redirect(url_for('admin_users'))

    if User.find_by_username(username):
        flash('Username is already taken.', 'warning')
        return redirect(url_for('admin_users'))

    new_user = User.create({
        'email': email_raw,
        'username': username,
        'full_name': full_name or username,
        'student_identifier': student_id or None if role == 'student' else None,
        'password_hash': generate_password_hash(password),
        'role': role,
        'is_active': True,
        'is_email_verified': True,
        'must_change_password': False,
        'account_source': 'admin_created'
    })

    try:
        login_endpoint = 'login_staff' if role == 'staff' else ('login_admin' if role == 'admin' else 'login_student')
        portal_login_url = f"{Config.APP_BASE_URL.rstrip('/')}/login/{role}"
        send_welcome_email(
            recipient_email=new_user.email,
            recipient_name=new_user.full_name or new_user.username,
            username=new_user.username,
            role=new_user.role,
            student_identifier=new_user.student_identifier,
            login_url=portal_login_url
        )
    except Exception as e:
        app.logger.warning(f"Could not send welcome email: {e}")

    AuditLog.log(
        event_type='STAFF_PROVISIONED' if role == 'staff' else 'USER_PROVISIONED',
        description=f'Admin {admin.username} provisioned {role} account: @{username} ({new_user.email})',
        actor_id=admin.id,
        actor_role=admin.role,
        target_type='User',
        target_id=new_user.id,
        ip_address=request.remote_addr
    )
    flash(f'{role.capitalize()} account for {new_user.full_name} provisioned successfully.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/removal-requests')
@login_required
@role_required('admin')
def admin_removal_requests():
    requests = RemovalRequest.find_all()
    return render_template('admin/removal_requests.html', requests=requests)

@app.route('/admin/removal-requests/<request_id>/action', methods=['POST'])
@login_required
@role_required('admin')
def admin_handle_removal_request(request_id):
    admin = get_current_user()
    req = RemovalRequest.find_by_id(request_id)
    if not req:
        abort(404)

    action = request.form.get('action')  # 'approve' or 'reject'
    if action == 'approve':
        req.review(admin.id, 'approved')

        # Soft update membership to removed
        mem = ClassroomMembership.find_one(req.classroom_id, req.student_id)
        if mem:
            from mongo_db import get_memberships_col
            get_memberships_col().update_one(
                {'_id': to_object_id(mem.id)},
                {'$set': {'status': 'removed', 'removed_at': datetime.now(timezone.utc)}}
            )

        AuditLog.log(
            event_type='STUDENT_REMOVED',
            description=f'Admin {admin.username} approved removal of student ID {req.student_id} from class ID {req.classroom_id}',
            actor_id=admin.id,
            actor_role=admin.role,
            target_type='RemovalRequest',
            target_id=req.id,
            ip_address=request.remote_addr
        )
        flash('Student removal request approved. Classroom membership updated.', 'success')
    elif action == 'reject':
        req.review(admin.id, 'rejected')
        AuditLog.log(
            event_type='REMOVAL_REQUEST_REJECTED',
            description=f'Admin {admin.username} rejected removal request #{req.id}',
            actor_id=admin.id,
            actor_role=admin.role,
            target_type='RemovalRequest',
            target_id=req.id,
            ip_address=request.remote_addr
        )
        flash('Student removal request rejected.', 'info')

    try:
        student_user = User.find_by_id(req.student_id)
        staff_user = User.find_by_id(req.staff_id)
        c_doc = Classroom.find_by_id(req.classroom_id)
        c_name = c_doc.name if c_doc else "Classroom"

        if student_user and student_user.email:
            send_removal_request_decision_email(
                recipient_email=student_user.email,
                student_name=student_user.full_name or student_user.username,
                classroom_name=c_name,
                action=action,
                reviewer_name=admin.full_name or admin.username
            )
        if staff_user and staff_user.email:
            send_removal_request_decision_email(
                recipient_email=staff_user.email,
                student_name=student_user.full_name if student_user else "Student",
                classroom_name=c_name,
                action=action,
                reviewer_name=admin.full_name or admin.username
            )
    except Exception as e:
        app.logger.warning(f"Could not send removal decision notifications: {e}")

    next_url = request.form.get('next') or request.referrer or url_for('admin_removal_requests')
    return redirect(next_url)

@app.route('/admin/audit-logs')
@login_required
@role_required('admin')
def admin_audit_logs():
    logs = AuditLog.find_all(limit=150)
    return render_template('admin/audit_logs.html', logs=logs)

@app.route('/admin/sync-all')
@login_required
@role_required('admin')
def admin_sync_all():
    admin = get_current_user()
    res = sync_all_portal_profiles(force=True)
    AuditLog.log(
        event_type='PORTAL_SYNC_TRIGGERED',
        description=f'Admin {admin.username} triggered full portal sync. Synced {res.get("total_profiles_synced", 0)} profiles.',
        actor_id=admin.id,
        actor_role=admin.role,
        ip_address=request.remote_addr
    )
    flash(f'Portal-wide sync completed! {res.get("total_profiles_synced", 0)} profiles synchronized.', 'success')
    return redirect(url_for('admin_dashboard'))

# ---------------------------------------------------------
# Excel Report Export Routes (Thread-Safe & Isolated)
# ---------------------------------------------------------
@app.route('/download/classroom/<classroom_id>')
@login_required
def download_classroom_report(classroom_id):
    user = get_current_user()
    classroom = verify_classroom_access(classroom_id, user)

    memberships = ClassroomMembership.find_by_classroom_id(classroom.id, status='active')
    students_with_profiles = []

    for m in memberships:
        s = m.student
        if not s:
            continue
        profiles = PlatformProfile.find_by_user_id(s.id)
        students_with_profiles.append({
            "student": s,
            "profiles": profiles
        })

    file_path, filename = generate_classroom_excel(classroom, students_with_profiles)
    return send_file(file_path, as_attachment=True, download_name=filename)

@app.route('/download/student/<student_id>')
@login_required
def download_student_report(student_id):
    user = get_current_user()
    student = verify_student_access(student_id, user)

    profiles = PlatformProfile.find_by_user_id(student.id)
    snapshots = PerformanceSnapshot.find_by_user_id(student.id, limit=30)
    snapshots.sort(key=lambda s: str(s.snapshot_date), reverse=True)

    file_path, filename = generate_student_excel(student, profiles, snapshots)
    return send_file(file_path, as_attachment=True, download_name=filename)

# ---------------------------------------------------------
# Application Entrypoint & Scheduler
# ---------------------------------------------------------
def scheduled_daily_sync():
    with app.app_context():
        print("[*] Running automated daily platform synchronization with MongoDB Atlas...")
        sync_all_portal_profiles(force=False)

if __name__ == '__main__':
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        try:
            scheduler.add_job(id='daily_platform_sync', func=scheduled_daily_sync, trigger='interval', days=1)
            scheduler.init_app(app)
            scheduler.start()
        except Exception as e:
            print(f"Scheduler initialization note: {e}")

    app.run(debug=True, host='127.0.0.1', port=5000)
