import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from config import Config

logger = logging.getLogger(__name__)

def is_smtp_configured():
    """Returns True if all required SMTP credentials are set in the environment."""
    return bool(Config.SMTP_HOST and Config.SMTP_USER and Config.SMTP_PASSWORD)

def _send_email_payload(recipient_email, subject, html_content, text_content, log_tag="EMAIL SERVICE"):
    """
    Core email delivery helper.
    Sends via live SMTP server (Gmail, Outlook, custom) if configured in .env.
    Otherwise, logs formatted email summary to the terminal in Developer Sandbox mode.
    """
    if is_smtp_configured():
        try:
            from_email = Config.SMTP_FROM_EMAIL if (Config.SMTP_FROM_EMAIL and Config.SMTP_FROM_EMAIL != 'no-reply@scoretracker.io') else (Config.SMTP_USER or 'no-reply@scoretracker.io')
            from_header = f"ScoreTracker Portal <{from_email}>"

            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = from_header
            msg['To'] = recipient_email

            part1 = MIMEText(text_content, 'plain')
            part2 = MIMEText(html_content, 'html')
            msg.attach(part1)
            msg.attach(part2)

            context = ssl.create_default_context()

            if Config.SMTP_PORT == 465:
                with smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT, context=context, timeout=15) as server:
                    server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
                    server.sendmail(from_email, [recipient_email], msg.as_string())
            else:
                with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as server:
                    server.ehlo()
                    if Config.SMTP_USE_TLS:
                        server.starttls(context=context)
                        server.ehlo()
                    server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
                    server.sendmail(from_email, [recipient_email], msg.as_string())

            logger.info(f"[{log_tag}] Successfully delivered SMTP email to {recipient_email} (Subject: '{subject}')")
            print(f"[SMTP SUCCESS] {log_tag} email delivered to {recipient_email} - '{subject}'")
            return True, "Email delivered successfully via SMTP."
        except Exception as e:
            logger.error(f"[{log_tag}] Failed to send SMTP email to {recipient_email}: {e}")
            print(f"\n=======================================================")
            print(f"[SMTP ERROR] Failed to deliver real email via SMTP: {e}")
            print(f"Recipient: {recipient_email}")
            print(f"Subject: {subject}")
            print(f"=======================================================\n")
            return True, f"Email prepared (Live SMTP failed: {e})"
    else:
        print(f"\n=======================================================")
        print(f"[DEV {log_tag.upper()}] Email Notification")
        print(f"To: {recipient_email}")
        print(f"Subject: {subject}")
        print(f"Text Preview:\n{text_content.strip()}")
        print(f"=======================================================\n")
        return True, "Email generated (Dev mode sandbox)."

def _wrap_html_template(badge_text, title_text, main_html, cta_url=None, cta_text="View in Portal", footer_note=None):
    """Wraps body HTML in the ScoreTracker dark-glass branded email template."""
    cta_btn = f"""
    <div style="text-align: center; margin: 24px 0 16px 0;">
        <a href="{cta_url}" style="display: inline-block; background: linear-gradient(135deg, #3b82f6, #2563eb); color: #ffffff !important; text-decoration: none; padding: 12px 28px; border-radius: 10px; font-weight: 700; font-size: 14px; box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);" target="_blank">
            {cta_text} &rarr;
        </a>
    </div>
    """ if cta_url else ""

    extra_footer = f"<br>{footer_note}" if footer_note else ""

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #090d16; color: #ffffff; padding: 24px; margin: 0; }}
            .container {{ max-width: 560px; margin: 0 auto; background: #141e33; border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 32px; box-shadow: 0 12px 40px rgba(0,0,0,0.5); }}
            .brand {{ font-size: 22px; font-weight: 800; color: #ffffff; margin-bottom: 20px; }}
            .brand span {{ color: #3b82f6; }}
            .badge {{ display: inline-block; background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); padding: 4px 12px; border-radius: 9999px; font-size: 12px; font-weight: 700; text-transform: uppercase; margin-bottom: 12px; }}
            .info-table {{ width: 100%; border-collapse: collapse; background: #0d1527; border-radius: 10px; overflow: hidden; margin: 18px 0; border: 1px solid rgba(255,255,255,0.08); }}
            .footer {{ font-size: 12px; color: #94a3b8; margin-top: 28px; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 16px; line-height: 1.5; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="brand">ScoreTracker<span>.io</span></div>
            <div class="badge">{badge_text}</div>
            <h2 style="margin: 0 0 12px 0; font-size: 22px; color: #ffffff;">{title_text}</h2>
            {main_html}
            {cta_btn}
            <div class="footer">
                &copy; 2026 Global Platform Score Tracker &bull; Automated Notification System{extra_footer}
            </div>
        </div>
    </body>
    </html>
    """

# -----------------------------------------------------------------------------
# 1. Welcome / Registration Email
# -----------------------------------------------------------------------------
def send_welcome_email(recipient_email, recipient_name, username, role, student_identifier=None, login_url=None):
    """Sends registration confirmation email containing account details and portal guide."""
    role_display = "Student" if role == 'student' else ("Faculty / Staff" if role == 'staff' else "Administrator")
    login_link = login_url or "http://127.0.0.1:5000/login"

    student_row_html = f"""
    <tr>
        <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student Roll / ID</td>
        <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{student_identifier}</td>
    </tr>
    """ if (role == 'student' and student_identifier) else ""

    student_row_text = f"Student ID / Roll: {student_identifier}\n" if (role == 'student' and student_identifier) else ""

    role_tips_html = """
    <div style="background: rgba(59, 130, 246, 0.08); border-left: 3px solid #3b82f6; padding: 14px 18px; border-radius: 8px; margin: 18px 0;">
        <h4 style="margin: 0 0 8px 0; color: #60a5fa; font-size: 14px; font-weight: 700;">Student Quick Start Checklist:</h4>
        <ul style="margin: 0; padding-left: 18px; color: #cbd5e1; font-size: 13px; line-height: 1.6;">
            <li><strong>Add Coding Handles:</strong> Link LeetCode, Codeforces, HackerRank, GitHub, CodeChef, and GeeksforGeeks profiles.</li>
            <li><strong>Join Classrooms:</strong> Enter invite codes from your instructors to join class cohorts and leaderboards.</li>
            <li><strong>Live Tracking:</strong> Real-time ratings and problem counts synchronize automatically.</li>
        </ul>
    </div>
    """ if role == 'student' else """
    <div style="background: rgba(16, 185, 129, 0.08); border-left: 3px solid #10b981; padding: 14px 18px; border-radius: 8px; margin: 18px 0;">
        <h4 style="margin: 0 0 8px 0; color: #34d399; font-size: 14px; font-weight: 700;">Faculty Quick Start Checklist:</h4>
        <ul style="margin: 0; padding-left: 18px; color: #cbd5e1; font-size: 13px; line-height: 1.6;">
            <li><strong>Create Classrooms:</strong> Create course cohorts and generate instant student Join Codes.</li>
            <li><strong>Sync Stats:</strong> Synchronize student contest ratings and problem statistics in batch.</li>
            <li><strong>Excel Export:</strong> Export complete roster analytics to Excel with a single click.</li>
        </ul>
    </div>
    """

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Your account on the <strong>Global Platform Score Tracker</strong> has been registered successfully.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Full Name</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{recipient_name or username}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Username</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #60a5fa; font-size: 14px; font-weight: 700; font-family: monospace;">@{username}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Registered Email</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{recipient_email}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Account Role</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #34d399; font-size: 14px; font-weight: 700;">{role_display}</td>
        </tr>
        {student_row_html}
    </table>
    {role_tips_html}
    """

    subject = f"Welcome to ScoreTracker.io — Account Registered (@{username})"
    html_content = _wrap_html_template("Registration Confirmed", f"Welcome, {recipient_name or username}!", body_html, cta_url=login_link, cta_text="Access Your Dashboard")

    text_content = f"""
Welcome to ScoreTracker.io!

Hi {recipient_name or username},
Your account on the Global Platform Score Tracker has been registered successfully.

--- ACCOUNT SUMMARY ---
Full Name: {recipient_name or username}
Username: @{username}
Email: {recipient_email}
Role: {role_display}
{student_row_text}Portal Login: {login_link}
-----------------------
"""
    return _send_email_payload(recipient_email, subject, html_content, text_content, log_tag="REGISTRATION WELCOME")

# -----------------------------------------------------------------------------
# 2. Classroom Enrollment Email (Student Notification)
# -----------------------------------------------------------------------------
def send_classroom_enrolled_email(student, classroom, staff, app_url="http://127.0.0.1:5000"):
    """Sends notification to student confirming enrollment in a classroom cohort."""
    if not student or not student.email:
        return False, "No student email"

    classroom_url = f"{app_url}/student/classroom/{classroom.id}"
    staff_name = staff.full_name or staff.username if staff else "Faculty Instructor"
    staff_email = staff.email if staff else "N/A"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        You have successfully joined the classroom cohort <strong>{classroom.name}</strong>.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom Name</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #60a5fa; font-size: 14px; font-weight: 700;">{classroom.name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Instructor</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{staff_name} ({staff_email})</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Description</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 13px;">{classroom.description or 'No specific description provided'}</td>
        </tr>
    </table>
    <div style="background: rgba(59, 130, 246, 0.08); border-left: 3px solid #3b82f6; padding: 12px 16px; border-radius: 8px; margin: 16px 0; font-size: 13px; color: #cbd5e1;">
        <i class="fas fa-info-circle text-primary"></i> Your competitive programming scores (LeetCode, Codeforces, HackerRank, GitHub, CodeChef, GeeksforGeeks) are automatically ranked on this class's leaderboard.
    </div>
    """

    subject = f"Enrolled in Classroom: {classroom.name} — ScoreTracker.io"
    html_content = _wrap_html_template("Classroom Enrollment", f"Classroom Joined: {classroom.name}", body_html, cta_url=classroom_url, cta_text="View Class Leaderboard")

    text_content = f"""
Classroom Enrollment Confirmed!

Hi {student.full_name or student.username},
You have joined the classroom: {classroom.name}
Instructor: {staff_name} ({staff_email})

View classroom leaderboard: {classroom_url}
"""
    return _send_email_payload(student.email, subject, html_content, text_content, log_tag="CLASSROOM JOIN STUDENT")

# -----------------------------------------------------------------------------
# 3. Student Joined Notification (Faculty / Staff Alert)
# -----------------------------------------------------------------------------
def send_student_joined_staff_notification_email(staff, student, classroom, app_url="http://127.0.0.1:5000"):
    """Sends notification to faculty instructor when a new student joins their classroom."""
    if not staff or not staff.email:
        return False, "No staff email"

    roster_url = f"{app_url}/staff/classroom/{classroom.id}"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        A new student has enrolled in your classroom cohort <strong>{classroom.name}</strong>.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student Name</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{student.full_name or student.username}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Username</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #60a5fa; font-size: 14px; font-weight: 700; font-family: monospace;">@{student.username}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student Email</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px;">{student.email}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student ID / Roll</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px;">{student.student_identifier or 'N/A'}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #34d399; font-size: 14px; font-weight: 700;">{classroom.name}</td>
        </tr>
    </table>
    """

    subject = f"New Student Enrolled in {classroom.name}: {student.full_name or student.username}"
    html_content = _wrap_html_template("Classroom Roster Update", f"New Student in {classroom.name}", body_html, cta_url=roster_url, cta_text="Manage Classroom Roster")

    text_content = f"""
New Student Joined Your Classroom!

Student: {student.full_name or student.username} (@{student.username})
Email: {student.email}
ID: {student.student_identifier or 'N/A'}
Classroom: {classroom.name}

Manage roster: {roster_url}
"""
    return _send_email_payload(staff.email, subject, html_content, text_content, log_tag="STUDENT JOIN STAFF ALERT")

# -----------------------------------------------------------------------------
# 4. Student Removal Request Submitted (Admin Notice)
# -----------------------------------------------------------------------------
def send_removal_request_submitted_email(admin_email, staff, student, classroom, reason, app_url="http://127.0.0.1:5000"):
    """Sends notification to Administrator when a faculty member submits a student removal request."""
    if not admin_email:
        return False, "No admin email"

    admin_queue_url = f"{app_url}/admin/dashboard"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Faculty instructor <strong>{staff.full_name or staff.username}</strong> has submitted a student removal request for administrator review.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{student.full_name or student.username} (@{student.username})</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #60a5fa; font-size: 14px; font-weight: 700;">{classroom.name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Requesting Faculty</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px;">{staff.full_name or staff.username} ({staff.email})</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Stated Reason</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #f59e0b; font-size: 13px; font-weight: 600;">{reason}</td>
        </tr>
    </table>
    """

    subject = f"Pending Action: Removal Request for {student.full_name or student.username} in {classroom.name}"
    html_content = _wrap_html_template("Action Required", "Student Removal Request Submitted", body_html, cta_url=admin_queue_url, cta_text="Review in Admin Queue")

    text_content = f"""
Student Removal Request Submitted

Student: {student.full_name or student.username} (@{student.username})
Classroom: {classroom.name}
Faculty: {staff.full_name or staff.username}
Reason: {reason}

Review queue: {admin_queue_url}
"""
    return _send_email_payload(admin_email, subject, html_content, text_content, log_tag="REMOVAL REQUEST ADMIN ALERT")

# -----------------------------------------------------------------------------
# 5. Removal Request Decision (Student & Staff Notification)
# -----------------------------------------------------------------------------
def send_removal_request_decision_email(recipient_email, recipient_name, student, classroom, action, reviewer=None, app_url="http://127.0.0.1:5000"):
    """Sends notification to user regarding administrator approval/rejection of a removal request."""
    if not recipient_email:
        return False, "No recipient email"

    is_approved = (action == 'approve')
    status_label = "Approved (Removed from Cohort)" if is_approved else "Rejected (Remains in Cohort)"
    status_color = "#ef4444" if is_approved else "#10b981"
    reviewer_name = reviewer.full_name or reviewer.username if reviewer else "Administrator"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        The removal request for student <strong>{student.full_name or student.username}</strong> in classroom <strong>{classroom.name}</strong> has been reviewed by <strong>{reviewer_name}</strong>.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{classroom.name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px;">{student.full_name or student.username} (@{student.username})</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Decision</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: {status_color}; font-size: 14px; font-weight: 700;">{status_label}</td>
        </tr>
    </table>
    """

    subject = f"Classroom Removal Request {action.capitalize()}: {classroom.name}"
    html_content = _wrap_html_template("Governance Update", f"Removal Request {action.capitalize()}", body_html, cta_url=f"{app_url}/login", cta_text="Go to Portal")

    text_content = f"""
Removal Request Decision

Classroom: {classroom.name}
Student: {student.full_name or student.username}
Decision: {status_label}
Reviewed by: {reviewer_name}
"""
    return _send_email_payload(recipient_email, subject, html_content, text_content, log_tag="REMOVAL DECISION NOTICE")

# -----------------------------------------------------------------------------
# 6. Password Reset Notification
# -----------------------------------------------------------------------------
def send_password_reset_notification_email(user, new_password=None, app_url="http://127.0.0.1:5000"):
    """Sends notification to user when an administrator updates their password."""
    if not user or not user.email:
        return False, "No user email"

    login_url = f"{app_url}/login"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Hello <strong>{user.full_name or user.username}</strong>, your ScoreTracker portal account password has been updated by the system administrator.
    </p>
    <div style="background: rgba(239, 68, 68, 0.08); border-left: 3px solid #ef4444; padding: 14px 18px; border-radius: 8px; margin: 16px 0; font-size: 13px; color: #cbd5e1;">
        <strong>Security Notice:</strong> If you did not request or expect this change, please contact your institution administrator immediately.
    </div>
    """

    subject = "ScoreTracker.io — Account Password Updated"
    html_content = _wrap_html_template("Security Alert", "Your Password Has Been Updated", body_html, cta_url=login_url, cta_text="Sign In to Your Account")

    text_content = f"""
Password Updated

Hello {user.full_name or user.username},
Your ScoreTracker.io account password has been updated by the administrator.

Sign in at: {login_url}
"""
    return _send_email_payload(user.email, subject, html_content, text_content, log_tag="PASSWORD RESET NOTICE")

# -----------------------------------------------------------------------------
# 7. Account Status Notification (Activated / Deactivated)
# -----------------------------------------------------------------------------
def send_account_status_notification_email(user, is_active, app_url="http://127.0.0.1:5000"):
    """Sends notification to user when their account is activated or deactivated."""
    if not user or not user.email:
        return False, "No user email"

    status_str = "Activated" if is_active else "Deactivated"
    color = "#10b981" if is_active else "#ef4444"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Hello <strong>{user.full_name or user.username}</strong>, your account status on the Global Platform Score Tracker has been <strong style="color: {color};">{status_str.lower()}</strong> by an administrator.
    </p>
    """

    subject = f"ScoreTracker.io Account Status Notice: {status_str}"
    html_content = _wrap_html_template("Account Notice", f"Account {status_str}", body_html, cta_url=f"{app_url}/login", cta_text="Portal Login")

    text_content = f"""
Account Status Notice

Hello {user.full_name or user.username},
Your account status has been updated to: {status_str}
"""
    return _send_email_payload(user.email, subject, html_content, text_content, log_tag="ACCOUNT STATUS NOTICE")

# -----------------------------------------------------------------------------
# 8. OTP Verification Email (Optional 2-Step verification)
# -----------------------------------------------------------------------------
def send_otp_email(recipient_email, recipient_name, otp_code):
    """Sends an OTP verification email to the user."""
    subject = f"{otp_code} is your ScoreTracker Verification Code"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px;">Hi {recipient_name or 'there'},</p>
    <p style="color: #cbd5e1; font-size: 14px;">Please use the following 6-digit verification code to complete your two-step login:</p>
    <div style="background: #0d1527; border: 2px dashed #3b82f6; border-radius: 12px; padding: 18px; text-align: center; margin: 20px 0; font-size: 32px; font-weight: 900; letter-spacing: 8px; color: #60a5fa; font-family: monospace;">
        {otp_code}
    </div>
    <p style="color: #94a3b8; font-size: 13px;">This code will expire in <strong>{Config.OTP_EXPIRE_MINUTES} minutes</strong>.</p>
    """
    html_content = _wrap_html_template("Two-Step Verification", "Verify Your Identity", body_html)
    text_content = f"Your ScoreTracker OTP is: {otp_code} (Expires in {Config.OTP_EXPIRE_MINUTES} mins)"

    return _send_email_payload(recipient_email, subject, html_content, text_content, log_tag="OTP VERIFICATION")
