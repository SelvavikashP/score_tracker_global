from functools import wraps
from flask import session, flash, redirect, url_for, abort, g, request
from models_mongo import User, Classroom, ClassroomMembership

def get_current_user():
    """Retrieves the authenticated user from MongoDB or flask.g context."""
    if 'current_user' in g:
        return g.current_user

    user_id = session.get('user_id')
    if not user_id:
        g.current_user = None
        return None

    user = User.find_by_id(user_id)
    if not user or not user.is_active:
        # Invalid or deactivated user
        session.clear()
        g.current_user = None
        return None

    g.current_user = user
    return user

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            session.clear()
            flash('Please sign in to access this page.', 'warning')
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    """
    Enforces server-side Role-Based Access Control (RBAC).
    Allowed roles can be e.g. 'admin', 'staff', 'student'.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user:
                flash('Please sign in to access this page.', 'warning')
                return redirect(url_for('login', next=request.path))
            
            if user.role not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def verify_classroom_ownership(classroom_id, user):
    """
    Ensures that the classroom belongs to the staff member or user is admin.
    Returns the Classroom object or aborts 404/403.
    """
    classroom = Classroom.find_by_id(classroom_id)
    if not classroom:
        abort(404)
    if user.role == 'admin':
        return classroom
    if user.role == 'staff' and str(classroom.staff_id) == str(user.id):
        return classroom
    abort(403)

def verify_classroom_access(classroom_id, user):
    """
    Ensures that the user has legitimate access to view the classroom:
    - Admin: Full access
    - Staff: Only if created by that staff
    - Student: Only if active member of the classroom
    """
    classroom = Classroom.find_by_id(classroom_id)
    if not classroom:
        abort(404)
    if user.role == 'admin':
        return classroom
    if user.role == 'staff' and str(classroom.staff_id) == str(user.id):
        return classroom
    if user.role == 'student':
        membership = ClassroomMembership.find_one(
            classroom_id=classroom.id,
            student_id=user.id
        )
        if membership and membership.status == 'active':
            return classroom
    abort(403)

def verify_student_access(student_id, user):
    """
    Ensures that the user has authorization to view/manage the student:
    - Admin: Full access
    - Student: Only their own account (IDOR defense)
    - Staff: Only students belonging to classrooms owned by this staff member
    """
    student = User.find_by_id(student_id)
    if not student or student.role != 'student':
        abort(404)
    if user.role == 'admin':
        return student
    if user.role == 'student' and str(user.id) == str(student.id):
        return student
    if user.role == 'staff':
        # Check if student is in any classroom created by this staff
        staff_classrooms = Classroom.find_by_staff_id(user.id, status='active')
        for c in staff_classrooms:
            m = ClassroomMembership.find_one(classroom_id=c.id, student_id=student.id)
            if m and m.status == 'active':
                return student
    abort(403)
