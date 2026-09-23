import os
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
from flask_apscheduler import APScheduler

from config import Config
from models import db, User, Classroom, ClassroomMembership, PlatformProfile, PerformanceSnapshot, RemovalRequest, AuditLog
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
from migration import run_database_migrations
from email_service import (
    send_otp_email, send_welcome_email, send_classroom_enrolled_email,
    send_student_joined_staff_notification_email, send_removal_request_submitted_email,
    send_removal_request_decision_email, send_password_reset_notification_email,
    send_account_status_notification_email, is_smtp_configured
)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
scheduler = APScheduler()

# Initialize DB migrations & schema checks
run_database_migrations(app)

@app.before_request
def load_user_context():
    g.current_user = get_current_user()

# ---------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------
@app.errorhandler(400)
def bad_request_error(e):
    return render_template('errors/error.html', error_code=400, error_title="Bad Request", error_message="The request could not be processed due to malformed syntax."), 400

@app.errorhandler(403)
def forbidden_error(e):
    return render_template('errors/error.html', error_code=403, error_title="Access Forbidden", error_message="You do not have permission to access this resource."), 403

@app.errorhandler(404)
def not_found_error(e):
    return render_template('errors/error.html', error_code=404, error_title="Page Not Found", error_message="The requested resource or page was not found."), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('errors/error.html', error_code=500, error_title="Server Error", error_message="An unexpected error occurred. Our team has been notified."), 500

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "connected"
    }), 200

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

@app.route('/leaderboard')
def public_leaderboard():
    # Fetch active students with their platform profiles
    students = User.query.filter_by(role='student', is_active=True).all()
    leaderboard_data = []

    for s in students:
        profiles = PlatformProfile.query.filter_by(user_id=s.id).all()
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

    # Sort descending by composite performance score
    leaderboard_data.sort(key=lambda x: (x['total_score'], x['peak_rating'], x['total_solved']), reverse=True)
    return render_template('index.html', leaderboard=leaderboard_data)

# ---------------------------------------------------------
# Authentication Routes (Role-Separated & Universal)
# ---------------------------------------------------------
def handle_role_login(target_role=None):
    if get_current_user():
        return redirect(url_for('index'))

    # Read role from query param, URL route, or form
    role = target_role or request.args.get('role') or 'student'
    if role not in ('student', 'staff', 'admin'):
        role = 'student'

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = (request.form.get('password') or '').strip()
        form_role = (request.form.get('target_role') or role).strip().lower()

        user = User.query.filter(User.email.ilike(email)).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('Your account is deactivated. Please contact the portal administrator.', 'danger')
                return redirect(url_for('login', role=form_role))

            # Role verification check
            if form_role and user.role != form_role:
                # User logged in via role-specific form with a different role
                if form_role == 'admin' and user.role != 'admin':
                    flash(f'Access denied: Account "@{user.username}" does not have Administrator privileges.', 'danger')
                    return redirect(url_for('login_admin'))
                elif form_role == 'staff' and user.role != 'staff' and user.role != 'admin':
                    flash(f'Access denied: Account "@{user.username}" is not registered as Faculty/Staff.', 'danger')
                    return redirect(url_for('login_staff'))

            # Check email verification for students and staff
            if Config.REQUIRE_EMAIL_VERIFICATION and not user.is_email_verified and user.role != 'admin':
                otp_code = user.generate_otp(Config.OTP_EXPIRE_MINUTES)
                db.session.commit()
                send_otp_email(user.email, user.full_name or user.username, otp_code)
                session['pending_verification_user_id'] = user.id
                session['pending_verification_email'] = user.email
                if is_smtp_configured():
                    flash('Please enter the 6-digit verification code sent to your email to continue.', 'info')
                else:
                    flash('Two-step verification required. Enter the verification code shown on screen to continue.', 'info')
                return redirect(url_for('verify_otp'))

            session.permanent = True
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()

            AuditLog.log(
                event_type='LOGIN',
                description=f'User {user.username} ({user.role}) logged in successfully.',
                actor_id=user.id,
                ip_address=request.remote_addr
            )

            flash(f'Welcome back, {user.full_name or user.username}!', 'success')
            next_url = request.args.get('next')
            if next_url and next_url.startswith('/'):
                return redirect(next_url)

            # Route to appropriate portal
            if user.role == 'student':
                return redirect(url_for('student_dashboard'))
            elif user.role == 'staff':
                return redirect(url_for('staff_dashboard'))
            elif user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password. Please try again.', 'danger')

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
    if get_current_user():
        return redirect(url_for('index'))

    initial_role = request.args.get('role', 'student').strip().lower()
    if initial_role not in ['student', 'staff']:
        initial_role = 'student'

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        username = (request.form.get('username') or '').strip()
        student_id = (request.form.get('student_identifier') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = (request.form.get('password') or '').strip()
        chosen_role = (request.form.get('role') or initial_role).strip().lower()
        if chosen_role not in ['student', 'staff']:
            chosen_role = 'student'

        if not username or not email or not password:
            flash('Username, email, and password are required.', 'danger')
            return redirect(url_for('signup', role=chosen_role))

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'warning')
            return redirect(url_for('signup', role=chosen_role))

        if User.query.filter(User.email.ilike(email)).first():
            flash('An account with this email already exists. Please sign in.', 'warning')
            return redirect(url_for('login_staff' if chosen_role == 'staff' else 'login_student'))

        if User.query.filter(User.username.ilike(username)).first():
            flash('Username is already taken. Please choose another username.', 'warning')
            return redirect(url_for('signup', role=chosen_role))

        new_user = User(
            username=username,
            full_name=full_name or username,
            student_identifier=student_id or None if chosen_role == 'student' else None,
            email=email,
            password_hash=generate_password_hash(password),
            role=chosen_role,
            is_active=True,
            is_email_verified=True,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(new_user)
        db.session.commit()

        AuditLog.log(
            event_type='USER_SIGNUP',
            description=f'{chosen_role.capitalize()} registered account: @{username} ({email})',
            actor_id=new_user.id,
            ip_address=request.remote_addr
        )

        # Send Welcome & Registration Confirmation Email containing account details
        try:
            portal_login_url = request.host_url.rstrip('/') + url_for('login_staff' if chosen_role == 'staff' else 'login_student')
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

        # Optional OTP verification if explicitly enabled
        if Config.REQUIRE_EMAIL_VERIFICATION:
            new_user.is_email_verified = False
            otp_code = new_user.generate_otp(Config.OTP_EXPIRE_MINUTES)
            db.session.commit()
            send_otp_email(new_user.email, new_user.full_name, otp_code)
            session['pending_verification_user_id'] = new_user.id
            session['pending_verification_email'] = new_user.email
            if is_smtp_configured():
                flash(f'Account created! A 6-digit verification code has been sent to {new_user.email}.', 'info')
            else:
                flash(f'Account created! (Dev Mode: Live email is unconfigured. Use the verification code shown below).', 'info')
            return redirect(url_for('verify_otp'))

        session.permanent = True
        session['user_id'] = new_user.id
        session['username'] = new_user.username
        session['role'] = new_user.role

        role_display = "Faculty/Staff" if new_user.role == 'staff' else "Student"
        flash(f'Welcome aboard, {new_user.full_name}! Your {role_display} account was created successfully. A registration summary has been sent to {new_user.email}.', 'success')
        next_url = request.args.get('next')
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect(url_for('staff_dashboard' if new_user.role == 'staff' else 'student_dashboard'))

    return render_template('signup.html', active_role=initial_role)

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if get_current_user():
        return redirect(url_for('index'))

    user_id = session.get('pending_verification_user_id')
    if not user_id:
        flash('No pending verification found. Please sign in or register.', 'warning')
        return redirect(url_for('login'))

    user = db.session.get(User, user_id)
    if not user:
        session.pop('pending_verification_user_id', None)
        session.pop('pending_verification_email', None)
        flash('User account not found. Please register again.', 'danger')
        return redirect(url_for('signup'))

    is_smtp = is_smtp_configured()
    dev_otp = user.otp_code if not is_smtp else None

    if request.method == 'POST':
        otp_code = (request.form.get('otp') or '').strip()
        if not otp_code or len(otp_code) != 6:
            flash('Please enter a valid 6-digit verification code.', 'warning')
            return render_template('verify_otp.html', user=user, config_smtp_configured=is_smtp, dev_otp_code=dev_otp)

        success, message = user.verify_otp(otp_code)
        db.session.commit()

        if not success:
            flash(message, 'danger')
            dev_otp = user.otp_code if not is_smtp else None
            return render_template('verify_otp.html', user=user, config_smtp_configured=is_smtp, dev_otp_code=dev_otp)

        # Verification succeeded! Complete session login
        session.pop('pending_verification_user_id', None)
        session.pop('pending_verification_email', None)
        session.permanent = True
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()

        AuditLog.log(
            event_type='EMAIL_VERIFIED',
            description=f'User @{user.username} ({user.role}) successfully verified their email address ({user.email}).',
            actor_id=user.id,
            ip_address=request.remote_addr
        )

        role_target = 'student_dashboard'
        if user.role == 'staff':
            role_target = 'staff_dashboard'
        elif user.role == 'admin':
            role_target = 'admin_dashboard'

        flash(f'Email verified successfully! Welcome to your dashboard, {user.full_name or user.username}.', 'success')
        return redirect(url_for(role_target))

    return render_template('verify_otp.html', user=user, config_smtp_configured=is_smtp, dev_otp_code=dev_otp)

@app.route('/resend-otp', methods=['POST'])
def resend_otp():
    user_id = session.get('pending_verification_user_id')
    if not user_id:
        flash('No pending verification found. Please sign in or register.', 'warning')
        return redirect(url_for('login'))

    user = db.session.get(User, user_id)
    if not user:
        flash('Account not found.', 'danger')
        return redirect(url_for('signup'))

    otp_code = user.generate_otp(Config.OTP_EXPIRE_MINUTES)
    db.session.commit()
    send_otp_email(user.email, user.full_name or user.username, otp_code)
    if is_smtp_configured():
        flash(f'A fresh 6-digit verification code has been sent to {user.email}.', 'info')
    else:
        flash(f'A fresh 6-digit verification code has been generated for {user.email}.', 'info')
    return redirect(url_for('verify_otp'))

@app.route('/logout')
def logout():
    user = get_current_user()
    if user:
        AuditLog.log(
            event_type='LOGOUT',
            description=f'User {user.username} logged out.',
            actor_id=user.id,
            ip_address=request.remote_addr
        )
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))

# ---------------------------------------------------------
# Classroom Join Invitation Routes
# ---------------------------------------------------------
@app.route('/classroom/join/<token>')
def join_classroom_landing(token):
    classroom = Classroom.query.filter_by(invite_token=token, status='active').first()
    if not classroom:
        flash('Invalid or expired classroom invitation link.', 'danger')
        return redirect(url_for('index'))

    user = get_current_user()
    is_member = False
    if user:
        is_member = ClassroomMembership.query.filter_by(
            classroom_id=classroom.id,
            student_id=user.id,
            status='active'
        ).first() is not None

    return render_template('classroom_join.html', classroom=classroom, is_member=is_member)

@app.route('/classroom/join/<token>/confirm', methods=['POST'])
@login_required
@role_required('student')
def join_classroom_submit(token):
    classroom = Classroom.query.filter_by(invite_token=token, status='active').first()
    if not classroom:
        flash('Classroom join link is invalid or has been revoked.', 'danger')
        return redirect(url_for('student_dashboard'))

    user = get_current_user()
    existing_membership = ClassroomMembership.query.filter_by(
        classroom_id=classroom.id,
        student_id=user.id
    ).first()

    if existing_membership:
        if existing_membership.status == 'removed':
            existing_membership.status = 'active'
            db.session.commit()
            flash(f'Re-enrolled into {classroom.name} successfully!', 'success')
        else:
            flash(f'You are already enrolled in {classroom.name}.', 'info')
    else:
        new_membership = ClassroomMembership(
            classroom_id=classroom.id,
            student_id=user.id,
            status='active',
            joined_at=datetime.now(timezone.utc)
        )
        db.session.add(new_membership)
        db.session.commit()

        AuditLog.log(
            event_type='CLASSROOM_JOIN',
            description=f'Student {user.username} joined classroom "{classroom.name}" via invite token.',
            actor_id=user.id,
            target_type='Classroom',
            target_id=classroom.id,
            ip_address=request.remote_addr
        )
        flash(f'Successfully enrolled into {classroom.name}!', 'success')

    # Send enrollment notification email to student and alert to faculty
    try:
        base_url = request.host_url.rstrip('/')
        send_classroom_enrolled_email(student=user, classroom=classroom, staff=classroom.staff, app_url=base_url)
        if classroom.staff:
            send_student_joined_staff_notification_email(staff=classroom.staff, student=user, classroom=classroom, app_url=base_url)
    except Exception as e:
        app.logger.warning(f"Failed to send classroom enrollment email notifications: {e}")

    return redirect(url_for('student_classroom_leaderboard', classroom_id=classroom.id))

# ---------------------------------------------------------
# Student Portal Routes
# ---------------------------------------------------------
@app.route('/student/dashboard')
@login_required
@role_required('student')
def student_dashboard():
    student = get_current_user()
    profiles = PlatformProfile.query.filter_by(user_id=student.id).all()
    classrooms = ClassroomMembership.query.filter_by(student_id=student.id, status='active').all()

    # Calculate summary metrics
    top_rating = max([p.rating for p in profiles], default=0)
    total_solved = sum([p.recent_problems for p in profiles])
    total_contests = sum([p.total_contests for p in profiles])

    stats = {
        "top_rating": top_rating,
        "total_solved": total_solved,
        "total_contests": total_contests,
        "total_profiles": len(profiles)
    }

    # Fetch daily snapshots for progress charts (last 30 days)
    snapshots = PerformanceSnapshot.query.filter_by(user_id=student.id).order_by(PerformanceSnapshot.snapshot_date.asc()).all()

    # Aggregate snapshots by date for clean chart lines
    date_map = {}
    for s in snapshots:
        d_str = s.snapshot_date.strftime('%b %d')
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

    existing = PlatformProfile.query.filter_by(user_id=student.id, platform=platform).first()
    if existing:
        flash(f'You already have a {platform} handle ({existing.handle}) connected. Disconnect it first to add a new handle.', 'warning')
        return redirect(url_for('student_dashboard'))

    new_profile = PlatformProfile(
        user_id=student.id,
        platform=platform,
        handle=handle,
        profile_url=canonical_url,
        sync_status='pending'
    )
    db.session.add(new_profile)
    db.session.commit()

    # Synchronize platform data immediately
    res = sync_single_platform_profile(new_profile, force=True)
    if res.get("success"):
        flash(f'Successfully connected and synced {platform} handle "{handle}"!', 'success')
    else:
        flash(f'Connected {platform} handle "{handle}", but initial sync failed: {res.get("error")}', 'warning')

    return redirect(url_for('student_dashboard'))

@app.route('/student/profile/sync/<int:profile_id>')
@login_required
@role_required('student')
def student_sync_profile(profile_id):
    student = get_current_user()
    profile = db.session.get(PlatformProfile, profile_id)
    if not profile or profile.user_id != student.id:
        abort(404)

    res = sync_single_platform_profile(profile, force=True)
    if res.get("success"):
        flash(f'Updated stats for {profile.platform} ({profile.handle})!', 'success')
    else:
        flash(f'Failed to refresh {profile.platform}: {res.get("error")}', 'warning')
    return redirect(url_for('student_dashboard'))

@app.route('/student/profile/delete/<int:profile_id>')
@login_required
@role_required('student')
def student_delete_profile(profile_id):
    student = get_current_user()
    profile = db.session.get(PlatformProfile, profile_id)
    if not profile or profile.user_id != student.id:
        abort(404)

    plat = profile.platform
    h = profile.handle
    db.session.delete(profile)
    db.session.commit()
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

@app.route('/student/classroom/<int:classroom_id>')
@login_required
def student_classroom_leaderboard(classroom_id):
    user = get_current_user()
    classroom = verify_classroom_access(classroom_id, user)

    memberships = ClassroomMembership.query.filter_by(classroom_id=classroom.id, status='active').all()
    student_scores = []

    for m in memberships:
        s = m.student
        if not s.is_active:
            continue
        profiles = PlatformProfile.query.filter_by(user_id=s.id).all()
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
        classrooms = Classroom.query.order_by(Classroom.created_at.desc()).all()
    else:
        classrooms = Classroom.query.filter_by(staff_id=staff.id, status='active').order_by(Classroom.created_at.desc()).all()

    class_ids = [c.id for c in classrooms]
    memberships = ClassroomMembership.query.filter(
        ClassroomMembership.classroom_id.in_(class_ids),
        ClassroomMembership.status == 'active'
    ).all() if class_ids else []

    unique_student_ids = list(set(m.student_id for m in memberships))
    students_list = User.query.filter(User.id.in_(unique_student_ids)).order_by(User.created_at.desc()).all() if unique_student_ids else []
    profiles_list = PlatformProfile.query.filter(PlatformProfile.user_id.in_(unique_student_ids)).all() if unique_student_ids else []

    if staff.role == 'admin':
        staff_removal_requests = RemovalRequest.query.order_by(
            (RemovalRequest.status == 'pending').desc(),
            RemovalRequest.created_at.desc()
        ).all()
    else:
        staff_removal_requests = RemovalRequest.query.filter(
            (RemovalRequest.staff_id == staff.id) | (RemovalRequest.classroom_id.in_(class_ids))
        ).order_by(
            (RemovalRequest.status == 'pending').desc(),
            RemovalRequest.created_at.desc()
        ).all() if class_ids else []

    pending_removals = [r for r in staff_removal_requests if r.status == 'pending']

    # Map student enrolled classrooms
    student_classrooms_map = {}
    for m in memberships:
        student_classrooms_map.setdefault(m.student_id, []).append(m.classroom.name)

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

    new_classroom = Classroom(
        name=name,
        description=description or None,
        staff_id=staff.id,
        status='active',
        created_at=datetime.now(timezone.utc)
    )
    new_classroom.generate_invite_token()
    db.session.add(new_classroom)
    db.session.commit()

    AuditLog.log(
        event_type='CLASSROOM_CREATE',
        description=f'Staff {staff.username} created classroom "{name}"',
        actor_id=staff.id,
        target_type='Classroom',
        target_id=new_classroom.id,
        ip_address=request.remote_addr
    )
    flash(f'Classroom "{name}" created successfully with an active join link!', 'success')
    return redirect(url_for('staff_classroom_detail', classroom_id=new_classroom.id))

@app.route('/staff/classroom/<int:classroom_id>')
@login_required
@role_required('staff', 'admin')
def staff_classroom_detail(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    memberships = ClassroomMembership.query.filter_by(classroom_id=classroom.id, status='active').all()
    students_data = []

    for m in memberships:
        s = m.student
        profiles = PlatformProfile.query.filter_by(user_id=s.id).all()
        peak_rating = max([p.rating for p in profiles], default=0)
        total_solved = sum([p.recent_problems for p in profiles])
        students_data.append({
            "student": s,
            "profiles": profiles,
            "peak_rating": peak_rating,
            "total_solved": total_solved
        })

    return render_template('staff/classroom.html', classroom=classroom, students_data=students_data)

@app.route('/staff/classroom/<int:classroom_id>/generate-link')
@login_required
@role_required('staff', 'admin')
def staff_classroom_generate_link(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)
    classroom.generate_invite_token()
    db.session.commit()

    AuditLog.log(
        event_type='JOIN_TOKEN_GENERATE',
        description=f'Generated new invitation token for classroom "{classroom.name}"',
        actor_id=staff.id,
        target_type='Classroom',
        target_id=classroom.id,
        ip_address=request.remote_addr
    )
    flash('Generated new classroom join link!', 'success')
    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

@app.route('/staff/classroom/<int:classroom_id>/revoke-link')
@login_required
@role_required('staff', 'admin')
def staff_classroom_revoke_link(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)
    classroom.revoke_invite_token()
    db.session.commit()

    AuditLog.log(
        event_type='JOIN_TOKEN_REVOKE',
        description=f'Revoked join token for classroom "{classroom.name}"',
        actor_id=staff.id,
        target_type='Classroom',
        target_id=classroom.id,
        ip_address=request.remote_addr
    )
    flash('Classroom join link has been revoked.', 'info')
    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

@app.route('/staff/classroom/<int:classroom_id>/add-student', methods=['POST'])
@login_required
@role_required('staff', 'admin')
def staff_classroom_add_student(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)
    ident = (request.form.get('student_identifier') or '').strip().lower()

    if not ident:
        flash('Student email or username is required.', 'danger')
        return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

    student = User.query.filter(
        (User.email.ilike(ident)) | (User.username.ilike(ident))
    ).first()

    if not student or student.role != 'student':
        flash(f'No registered student found with email/username "{ident}".', 'danger')
        return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

    existing = ClassroomMembership.query.filter_by(
        classroom_id=classroom.id,
        student_id=student.id
    ).first()

    if existing:
        if existing.status == 'removed':
            existing.status = 'active'
            db.session.commit()
            flash(f'Re-enrolled {student.full_name or student.username} into {classroom.name}.', 'success')
        else:
            flash(f'{student.full_name or student.username} is already enrolled in this class.', 'info')
    else:
        membership = ClassroomMembership(
            classroom_id=classroom.id,
            student_id=student.id,
            status='active',
            joined_at=datetime.now(timezone.utc)
        )
        db.session.add(membership)
        db.session.commit()

        AuditLog.log(
            event_type='STUDENT_ADDED_BY_STAFF',
            description=f'Staff {staff.username} added student {student.username} to classroom "{classroom.name}"',
            actor_id=staff.id,
            target_type='Classroom',
            target_id=classroom.id,
            ip_address=request.remote_addr
        )
        flash(f'Enrolled {student.full_name or student.username} into {classroom.name}.', 'success')

    try:
        base_url = request.host_url.rstrip('/')
        send_classroom_enrolled_email(student=student, classroom=classroom, staff=staff, app_url=base_url)
    except Exception as e:
        app.logger.warning(f"Failed to send classroom enrollment email: {e}")

    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

@app.route('/staff/user/create', methods=['POST'])
@login_required
@role_required('staff', 'admin')
def staff_create_student_user():
    staff = get_current_user()
    full_name = (request.form.get('full_name') or '').strip()
    username = (request.form.get('username') or '').strip()
    student_id = (request.form.get('student_identifier') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    password = (request.form.get('password') or '').strip()
    classroom_id = request.form.get('classroom_id')

    if not username or not email or not password:
        flash('Username, email, and password are required to create a student account.', 'danger')
        return redirect(request.referrer or url_for('staff_dashboard'))

    if len(password) < 6:
        flash('Password must be at least 6 characters long.', 'warning')
        return redirect(request.referrer or url_for('staff_dashboard'))

    if User.query.filter(User.email.ilike(email)).first():
        flash('An account with this email already exists.', 'warning')
        return redirect(request.referrer or url_for('staff_dashboard'))

    if User.query.filter(User.username.ilike(username)).first():
        flash('Username is already taken. Please choose another username.', 'warning')
        return redirect(request.referrer or url_for('staff_dashboard'))

    new_student = User(
        username=username,
        full_name=full_name or username,
        student_identifier=student_id or None,
        email=email,
        password_hash=generate_password_hash(password),
        role='student',
        is_active=True,
        is_email_verified=True,  # Verified directly by authorized staff
        created_at=datetime.now(timezone.utc)
    )
    db.session.add(new_student)
    db.session.commit()

    try:
        portal_login_url = request.host_url.rstrip('/') + url_for('login_student')
        send_welcome_email(
            recipient_email=new_student.email,
            recipient_name=new_student.full_name or new_student.username,
            username=new_student.username,
            role=new_student.role,
            student_identifier=new_student.student_identifier,
            login_url=portal_login_url
        )
    except Exception as e:
        app.logger.warning(f"Could not send welcome email: {e}")

    # Optional classroom auto-enrollment
    assigned_classroom = None
    if classroom_id and str(classroom_id).isdigit():
        classroom = db.session.get(Classroom, int(classroom_id))
        if classroom and (staff.role == 'admin' or classroom.staff_id == staff.id):
            membership = ClassroomMembership(
                classroom_id=classroom.id,
                student_id=new_student.id,
                status='active',
                joined_at=datetime.now(timezone.utc)
            )
            db.session.add(membership)
            db.session.commit()
            assigned_classroom = classroom

    AuditLog.log(
        event_type='STAFF_PROVISIONED_STUDENT',
        description=f'Staff {staff.username} provisioned student login account: @{username} ({email})' + (f' in "{assigned_classroom.name}"' if assigned_classroom else ''),
        actor_id=staff.id,
        target_type='User',
        target_id=new_student.id,
        ip_address=request.remote_addr
    )

    enrollment_msg = f' and enrolled in {assigned_classroom.name}' if assigned_classroom else ''
    flash(f'Student login account for {new_student.full_name} (@{username}) created successfully{enrollment_msg}! The student can now sign in.', 'success')

    if assigned_classroom:
        try:
            base_url = request.host_url.rstrip('/')
            send_classroom_enrolled_email(student=new_student, classroom=assigned_classroom, staff=staff, app_url=base_url)
        except Exception as e:
            app.logger.warning(f"Failed to send classroom enrollment email: {e}")
        return redirect(url_for('staff_classroom_detail', classroom_id=assigned_classroom.id))
    return redirect(request.referrer or url_for('staff_dashboard'))

@app.route('/staff/classroom/<int:classroom_id>/leaderboard')
@login_required
@role_required('staff', 'admin')
def staff_classroom_leaderboard(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)

    memberships = ClassroomMembership.query.filter_by(classroom_id=classroom.id, status='active').all()
    student_scores = []

    for m in memberships:
        s = m.student
        if not s.is_active:
            continue
        profiles = PlatformProfile.query.filter_by(user_id=s.id).all()
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

@app.route('/staff/student/<int:student_id>')
@login_required
@role_required('staff', 'admin')
def staff_student_detail(student_id):
    staff = get_current_user()
    student = verify_student_access(student_id, staff)

    profiles = PlatformProfile.query.filter_by(user_id=student.id).all()
    snapshots = PerformanceSnapshot.query.filter_by(user_id=student.id).order_by(PerformanceSnapshot.snapshot_date.desc()).all()

    # Chart datasets
    date_map = {}
    for s in reversed(snapshots):
        d_str = s.snapshot_date.strftime('%b %d')
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

@app.route('/staff/student/<int:student_id>/request-removal', methods=['POST'])
@login_required
@role_required('staff')
def staff_request_removal(student_id):
    staff = get_current_user()
    student = verify_student_access(student_id, staff)
    classroom_id = int(request.form.get('classroom_id', 0))
    reason = (request.form.get('reason') or '').strip()

    classroom = verify_classroom_ownership(classroom_id, staff)
    if not reason:
        flash('Reason for removal is required.', 'danger')
        return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

    req = RemovalRequest(
        staff_id=staff.id,
        student_id=student.id,
        classroom_id=classroom.id,
        reason=reason,
        status='pending',
        created_at=datetime.now(timezone.utc)
    )
    db.session.add(req)
    db.session.commit()

    AuditLog.log(
        event_type='REMOVAL_REQUEST_SUBMITTED',
        description=f'Staff {staff.username} submitted removal request for student {student.username} from class "{classroom.name}"',
        actor_id=staff.id,
        target_type='RemovalRequest',
        target_id=req.id,
        ip_address=request.remote_addr
    )

    try:
        base_url = request.host_url.rstrip('/')
        admin_user = User.query.filter_by(role='admin').first()
        admin_email = admin_user.email if admin_user else Config.INITIAL_ADMIN_EMAIL
        send_removal_request_submitted_email(
            admin_email=admin_email,
            staff=staff,
            student=student,
            classroom=classroom,
            reason=reason,
            app_url=base_url
        )
    except Exception as e:
        app.logger.warning(f"Failed to send removal request admin notification: {e}")

    flash(f'Removal request for {student.full_name or student.username} submitted to Administrator.', 'info')
    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

@app.route('/staff/sync/classroom/<int:classroom_id>')
@login_required
@role_required('staff', 'admin')
def staff_sync_classroom(classroom_id):
    staff = get_current_user()
    classroom = verify_classroom_ownership(classroom_id, staff)
    res = sync_classroom_profiles(classroom.id, force=True)
    flash(f'Class sync completed: {res.get("synced_profiles", 0)} of {res.get("total_profiles", 0)} profiles updated.', 'success')
    return redirect(url_for('staff_classroom_detail', classroom_id=classroom.id))

@app.route('/staff/sync/student/<int:student_id>')
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
    students_list = User.query.filter_by(role='student').order_by(User.created_at.desc()).all()
    staff_list = User.query.filter_by(role='staff').order_by(User.created_at.desc()).all()
    classrooms_list = Classroom.query.order_by(Classroom.created_at.desc()).all()
    pending_removals_list = RemovalRequest.query.filter_by(status='pending').order_by(RemovalRequest.created_at.desc()).all()
    all_removals_list = RemovalRequest.query.order_by(
        (RemovalRequest.status == 'pending').desc(),
        RemovalRequest.created_at.desc()
    ).limit(25).all()
    recent_logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(8).all()

    # Map staff created classrooms count
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
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/export')
@login_required
@role_required('admin')
def admin_export_users():
    admin = get_current_user()
    users = User.query.order_by(User.id.asc()).all()
    file_path, filename = generate_user_directory_excel(users)

    AuditLog.log(
        event_type='USER_DIRECTORY_EXPORTED',
        description=f'Admin {admin.username} exported full user directory to Excel ({len(users)} accounts)',
        actor_id=admin.id,
        ip_address=request.remote_addr
    )
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/admin/user/<int:user_id>/reset-password', methods=['POST'])
@login_required
@role_required('admin')
def admin_reset_user_password(user_id):
    admin = get_current_user()
    target_user = db.session.get(User, user_id)
    if not target_user:
        flash('User account not found.', 'danger')
        return redirect(url_for('admin_users'))

    new_password = (request.form.get('new_password') or '').strip()
    if not new_password or len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'danger')
        return redirect(url_for('admin_users'))

    target_user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    try:
        base_url = request.host_url.rstrip('/')
        send_password_reset_notification_email(target_user, new_password=new_password, app_url=base_url)
    except Exception as e:
        app.logger.warning(f"Failed to send password reset email: {e}")

    AuditLog.log(
        event_type='ADMIN_PASSWORD_RESET',
        description=f'Admin {admin.username} reset password for user {target_user.username} (ID: {target_user.id})',
        actor_id=admin.id,
        target_type='User',
        target_id=target_user.id,
        ip_address=request.remote_addr
    )
    flash(f'Password for @{target_user.username} ({target_user.email}) has been updated successfully.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_user(user_id):
    admin = get_current_user()
    target_user = db.session.get(User, user_id)
    if not target_user:
        flash('User account not found.', 'danger')
        return redirect(url_for('admin_users'))

    if target_user.id == admin.id:
        flash('Cannot delete your own active administrator account.', 'warning')
        return redirect(url_for('admin_users'))

    user_username = target_user.username
    user_email = target_user.email
    user_role = target_user.role

    # Clean up associated removal requests where target user was student or staff or reviewer
    RemovalRequest.query.filter(
        (RemovalRequest.student_id == target_user.id) |
        (RemovalRequest.staff_id == target_user.id) |
        (RemovalRequest.reviewed_by_id == target_user.id)
    ).delete(synchronize_session=False)

    # Detach audit logs created by this user to avoid FK integrity violations
    AuditLog.query.filter_by(actor_id=target_user.id).update({'actor_id': None}, synchronize_session=False)

    # Delete the user (cascades memberships, profiles, snapshots, created classrooms)
    db.session.delete(target_user)
    db.session.commit()

    AuditLog.log(
        event_type='USER_PERMANENTLY_DELETED',
        description=f'Admin {admin.username} permanently deleted user account @{user_username} ({user_email}, role: {user_role}) from database',
        actor_id=admin.id,
        target_type='User',
        target_id=user_id,
        ip_address=request.remote_addr
    )
    flash(f'User account @{user_username} ({user_email}) has been permanently deleted from the database.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/toggle-status', methods=['POST'])
@login_required
@role_required('admin')
def admin_toggle_user_status(user_id):
    admin = get_current_user()
    target_user = db.session.get(User, user_id)
    if not target_user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_users'))

    if target_user.id == admin.id:
        flash('You cannot deactivate your own active admin account.', 'danger')
        return redirect(url_for('admin_users'))

    target_user.is_active = not target_user.is_active
    db.session.commit()

    try:
        base_url = request.host_url.rstrip('/')
        send_account_status_notification_email(target_user, is_active=target_user.is_active, app_url=base_url)
    except Exception as e:
        app.logger.warning(f"Failed to send account status notification: {e}")

    status_str = "activated" if target_user.is_active else "deactivated"
    AuditLog.log(
        event_type='USER_STATUS_TOGGLED',
        description=f'Admin {admin.username} {status_str} user {target_user.username} ({target_user.email})',
        actor_id=admin.id,
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
    email = (request.form.get('email') or '').strip().lower()
    password = (request.form.get('password') or '').strip()
    role = (request.form.get('role') or 'staff').strip().lower()
    student_id = (request.form.get('student_identifier') or '').strip()

    if role not in ['staff', 'student', 'admin']:
        role = 'staff'

    if not username or not email or not password:
        flash('Full name, username, email, and password are required.', 'danger')
        return redirect(url_for('admin_users'))

    if len(password) < 6:
        flash('Password must be at least 6 characters long.', 'warning')
        return redirect(url_for('admin_users'))

    if User.query.filter(User.email.ilike(email)).first():
        flash('An account with this email already exists.', 'warning')
        return redirect(url_for('admin_users'))

    if User.query.filter(User.username.ilike(username)).first():
        flash('Username is already taken.', 'warning')
        return redirect(url_for('admin_users'))

    new_user = User(
        username=username,
        full_name=full_name or username,
        student_identifier=student_id or None if role == 'student' else None,
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
        is_active=True,
        is_email_verified=True,  # Directly verified when provisioned by admin
        created_at=datetime.now(timezone.utc)
    )
    db.session.add(new_user)
    db.session.commit()

    try:
        login_endpoint = 'login_staff' if role == 'staff' else ('login_admin' if role == 'admin' else 'login_student')
        portal_login_url = request.host_url.rstrip('/') + url_for(login_endpoint)
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
        description=f'Admin {admin.username} provisioned {role} account: @{username} ({email})',
        actor_id=admin.id,
        target_type='User',
        target_id=new_user.id,
        ip_address=request.remote_addr
    )
    if role == 'staff':
        flash(f'Staff account for {new_user.full_name} provisioned successfully.', 'success')
    else:
        flash(f'{role.capitalize()} account for {new_user.full_name} (@{username}) provisioned successfully.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/removal-requests')
@login_required
@role_required('admin')
def admin_removal_requests():
    requests = RemovalRequest.query.order_by(
        (RemovalRequest.status == 'pending').desc(),
        RemovalRequest.created_at.desc()
    ).all()
    return render_template('admin/removal_requests.html', requests=requests)

@app.route('/admin/removal-requests/<int:request_id>/action', methods=['POST'])
@login_required
@role_required('admin')
def admin_handle_removal_request(request_id):
    admin = get_current_user()
    req = db.session.get(RemovalRequest, request_id)
    if not req:
        abort(404)

    action = request.form.get('action')  # 'approve' or 'reject'
    if action == 'approve':
        req.status = 'approved'
        req.reviewed_by_id = admin.id
        req.reviewed_at = datetime.now(timezone.utc)

        # Remove student from classroom membership (soft status change)
        membership = ClassroomMembership.query.filter_by(
            classroom_id=req.classroom_id,
            student_id=req.student_id
        ).first()
        if membership:
            membership.status = 'removed'

        db.session.commit()
        AuditLog.log(
            event_type='REMOVAL_REQUEST_APPROVED',
            description=f'Admin {admin.username} approved removal of student {req.student_member.username} from class "{req.classroom.name}"',
            actor_id=admin.id,
            target_type='RemovalRequest',
            target_id=req.id,
            ip_address=request.remote_addr
        )
        flash('Student removal request approved. Classroom membership updated.', 'success')
    elif action == 'reject':
        req.status = 'rejected'
        req.reviewed_by_id = admin.id
        req.reviewed_at = datetime.now(timezone.utc)
        db.session.commit()

        AuditLog.log(
            event_type='REMOVAL_REQUEST_REJECTED',
            description=f'Admin {admin.username} rejected removal request #{req.id}',
            actor_id=admin.id,
            target_type='RemovalRequest',
            target_id=req.id,
            ip_address=request.remote_addr
        )
        flash('Student removal request rejected.', 'info')

    # Send decision emails to student and faculty
    try:
        base_url = request.host_url.rstrip('/')
        if req.student_member and req.student_member.email:
            send_removal_request_decision_email(
                recipient_email=req.student_member.email,
                recipient_name=req.student_member.full_name or req.student_member.username,
                student=req.student_member,
                classroom=req.classroom,
                action=action,
                reviewer=admin,
                app_url=base_url
            )
        if req.staff_member and req.staff_member.email:
            send_removal_request_decision_email(
                recipient_email=req.staff_member.email,
                recipient_name=req.staff_member.full_name or req.staff_member.username,
                student=req.student_member,
                classroom=req.classroom,
                action=action,
                reviewer=admin,
                app_url=base_url
            )
    except Exception as e:
        app.logger.warning(f"Failed to send removal decision email notification: {e}")

    next_url = request.form.get('next') or request.referrer or url_for('admin_removal_requests')
    return redirect(next_url)

@app.route('/admin/audit-logs')
@login_required
@role_required('admin')
def admin_audit_logs():
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(150).all()
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
        ip_address=request.remote_addr
    )
    flash(f'Portal-wide sync completed! {res.get("total_profiles_synced", 0)} profiles synchronized.', 'success')
    return redirect(url_for('admin_dashboard'))

# ---------------------------------------------------------
# Excel Report Export Routes (Thread-Safe & Isolated)
# ---------------------------------------------------------
@app.route('/download/classroom/<int:classroom_id>')
@login_required
def download_classroom_report(classroom_id):
    user = get_current_user()
    classroom = verify_classroom_access(classroom_id, user)

    memberships = ClassroomMembership.query.filter_by(classroom_id=classroom.id, status='active').all()
    students_with_profiles = []

    for m in memberships:
        s = m.student
        profiles = PlatformProfile.query.filter_by(user_id=s.id).all()
        students_with_profiles.append({
            "student": s,
            "profiles": profiles
        })

    file_path, filename = generate_classroom_excel(classroom, students_with_profiles)
    return send_file(file_path, as_attachment=True, download_name=filename)

@app.route('/download/student/<int:student_id>')
@login_required
def download_student_report(student_id):
    user = get_current_user()
    student = verify_student_access(student_id, user)

    profiles = PlatformProfile.query.filter_by(user_id=student.id).all()
    snapshots = PerformanceSnapshot.query.filter_by(user_id=student.id).order_by(PerformanceSnapshot.snapshot_date.desc()).all()

    file_path, filename = generate_student_excel(student, profiles, snapshots)
    return send_file(file_path, as_attachment=True, download_name=filename)

# ---------------------------------------------------------
# Application Entrypoint & Scheduler
# ---------------------------------------------------------
def scheduled_daily_sync():
    with app.app_context():
        print("[*] Running automated daily platform synchronization...")
        sync_all_portal_profiles(force=False)

if __name__ == '__main__':
    # Scheduler runs only in primary worker / local development
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        try:
            scheduler.add_job(id='daily_platform_sync', func=scheduled_daily_sync, trigger='interval', days=1)
            scheduler.init_app(app)
            scheduler.start()
        except Exception as e:
            print(f"Scheduler initialization note: {e}")

    app.run(debug=True, host='127.0.0.1', port=5000)
