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
    Sends via live SMTP server (Gmail, SendGrid, custom) if configured in .env.
    Otherwise, logs safe delivery summary to the terminal in Developer Sandbox mode.
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
            print(f"[SMTP ERROR] Failed to deliver email to {recipient_email}: {e}")
            return False, f"Live SMTP delivery failed: {e}"
    else:
        # Safe sandbox logging without printing plaintext secrets
        print(f"[DEV SANDBOX] {log_tag} -> Dispatched simulated email to {recipient_email} (Subject: '{subject}')")
        return True, "Email simulated (Developer Sandbox mode)."

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
def send_welcome_email(recipient_email, recipient_name, username, role, student_identifier=None, login_url=None, is_newly_provisioned=False, default_password=None):
    """Sends registration confirmation email containing account details and portal guide."""
    role_display = "Student" if role == 'student' else ("Faculty / Staff" if role == 'staff' else "Administrator")
    login_link = login_url or f"{Config.APP_BASE_URL}/login/{role if role in ('student', 'staff', 'admin') else 'student'}"

    student_row_html = f"""
    <tr>
        <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student Roll / ID</td>
        <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{student_identifier}</td>
    </tr>
    """ if (role == 'student' and student_identifier) else ""

    credentials_notice_html = f"""
    <div style="background: rgba(245, 158, 11, 0.1); border-left: 3px solid #f59e0b; padding: 14px 18px; border-radius: 8px; margin: 18px 0;">
        <h4 style="margin: 0 0 6px 0; color: #fbbf24; font-size: 14px; font-weight: 700;">Initial Login Credentials:</h4>
        <p style="margin: 0 0 4px 0; color: #cbd5e1; font-size: 13px;">
            <strong>Registered Email:</strong> <code style="color: #60a5fa;">{recipient_email}</code><br>
            <strong>Temporary Password:</strong> <code style="color: #fbbf24;">{default_password or Config.DEFAULT_STUDENT_PASSWORD}</code>
        </p>
        <p style="margin: 6px 0 0 0; color: #f87171; font-size: 12px; font-weight: 600;">
            <i class="fas fa-exclamation-triangle"></i> Mandatory: You will be required to change your password immediately upon your first sign in.
        </p>
    </div>
    """ if (is_newly_provisioned and role == 'student') else ""

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Your account on the <strong>Global Platform Score Tracker</strong> has been provisioned successfully.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Full Name</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{recipient_name or username}</td>
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
    {credentials_notice_html}
    """

    subject = f"Welcome to ScoreTracker.io — Account Provisioned"
    html_content = _wrap_html_template("Registration Confirmed", f"Welcome, {recipient_name or username}!", body_html, cta_url=login_link, cta_text="Sign In to Portal")

    text_content = f"""
Welcome to ScoreTracker.io!

Hi {recipient_name or username},
Your account on the Global Platform Score Tracker has been provisioned.

Registered Email: {recipient_email}
Role: {role_display}
Portal Login: {login_link}
"""
    return _send_email_payload(recipient_email, subject, html_content, text_content, log_tag="REGISTRATION WELCOME")

# -----------------------------------------------------------------------------
# 2. Classroom Enrollment Email (Student Notification)
# -----------------------------------------------------------------------------
def send_classroom_enrolled_email(student_email, student_name, classroom_name, staff_name="Faculty Instructor", classroom_url=None, is_new_student=False, default_password=None):
    """Sends notification to student confirming enrollment in a classroom cohort."""
    if not student_email:
        return False, "No student email"

    target_url = classroom_url or f"{Config.APP_BASE_URL}/student/dashboard"

    credentials_notice = f"""
    <div style="background: rgba(245, 158, 11, 0.1); border-left: 3px solid #f59e0b; padding: 12px 16px; border-radius: 8px; margin: 16px 0; font-size: 13px; color: #cbd5e1;">
        <strong>First Time Logging In?</strong> Your temporary password is <code style="color: #fbbf24;">{default_password or Config.DEFAULT_STUDENT_PASSWORD}</code>. You must change your password on first login.
    </div>
    """ if is_new_student else """
    <p style="color: #94a3b8; font-size: 13px; margin-top: 12px;">
        Sign in with your existing ScoreTracker email and password to view your rankings.
    </p>
    """

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        You have been enrolled in the classroom cohort <strong>{classroom_name}</strong> by <strong>{staff_name}</strong>.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom Name</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #60a5fa; font-size: 14px; font-weight: 700;">{classroom_name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Instructor</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{staff_name}</td>
        </tr>
    </table>
    {credentials_notice}
    """

    subject = f"Enrolled in Classroom: {classroom_name} — ScoreTracker.io"
    html_content = _wrap_html_template("Classroom Enrollment", f"Classroom Joined: {classroom_name}", body_html, cta_url=target_url, cta_text="View Classroom Dashboard")

    text_content = f"""
Classroom Enrollment Confirmed!

Hi {student_name},
You have been enrolled in: {classroom_name} by {staff_name}.
Login URL: {Config.APP_BASE_URL}/login/student
"""
    return _send_email_payload(student_email, subject, html_content, text_content, log_tag="CLASSROOM JOIN STUDENT")

# -----------------------------------------------------------------------------
# 3. Student Joined Notification (Faculty / Staff Alert)
# -----------------------------------------------------------------------------
def send_student_joined_staff_notification_email(staff_email, staff_name, student_name, student_email, classroom_name, student_identifier=None, roster_url=None):
    """Sends notification to faculty instructor when a new student is enrolled."""
    if not staff_email:
        return False, "No staff email"

    target_url = roster_url or f"{Config.APP_BASE_URL}/staff/dashboard"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        A student has been enrolled in your classroom cohort <strong>{classroom_name}</strong>.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student Name</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{student_name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student Email</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #60a5fa; font-size: 14px;">{student_email}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student ID / Roll</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px;">{student_identifier or 'N/A'}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #34d399; font-size: 14px; font-weight: 700;">{classroom_name}</td>
        </tr>
    </table>
    """

    subject = f"New Student Enrolled in {classroom_name}: {student_name}"
    html_content = _wrap_html_template("Classroom Roster Update", f"New Student in {classroom_name}", body_html, cta_url=target_url, cta_text="Manage Classroom Roster")

    text_content = f"""
Student Enrolled:
Student: {student_name} ({student_email})
Classroom: {classroom_name}
"""
    return _send_email_payload(staff_email, subject, html_content, text_content, log_tag="STUDENT JOIN STAFF ALERT")

# -----------------------------------------------------------------------------
# 4. Student Removal Request Submitted (Admin Notice)
# -----------------------------------------------------------------------------
def send_removal_request_submitted_email(admin_email, staff_name, student_name, classroom_name, reason):
    """Sends notification to Administrator when a faculty member submits a student removal request."""
    if not admin_email:
        return False, "No admin email"

    admin_queue_url = f"{Config.APP_BASE_URL}/admin/dashboard"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Faculty instructor <strong>{staff_name}</strong> has submitted a student removal request for administrator review.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{student_name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #60a5fa; font-size: 14px; font-weight: 700;">{classroom_name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Requesting Faculty</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px;">{staff_name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Reason</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #f59e0b; font-size: 13px; font-weight: 600;">{reason}</td>
        </tr>
    </table>
    """

    subject = f"Pending Action: Removal Request for {student_name} in {classroom_name}"
    html_content = _wrap_html_template("Action Required", "Student Removal Request Submitted", body_html, cta_url=admin_queue_url, cta_text="Review in Admin Queue")

    text_content = f"""
Student Removal Request:
Student: {student_name}
Classroom: {classroom_name}
Faculty: {staff_name}
Reason: {reason}
"""
    return _send_email_payload(admin_email, subject, html_content, text_content, log_tag="REMOVAL REQUEST ADMIN ALERT")

# -----------------------------------------------------------------------------
# 5. Removal Request Decision (Student & Staff Notification)
# -----------------------------------------------------------------------------
def send_removal_request_decision_email(recipient_email, student_name, classroom_name, action, reviewer_name="Administrator"):
    """Sends notification regarding administrator approval/rejection of a removal request."""
    if not recipient_email:
        return False, "No recipient email"

    is_approved = (action == 'approve')
    status_label = "Approved (Removed from Cohort)" if is_approved else "Rejected (Remains in Cohort)"
    status_color = "#ef4444" if is_approved else "#10b981"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        The removal request for student <strong>{student_name}</strong> in classroom <strong>{classroom_name}</strong> has been reviewed by <strong>{reviewer_name}</strong>.
    </p>
    <table class="info-table">
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Classroom</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px; font-weight: 600;">{classroom_name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Student</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #ffffff; font-size: 14px;">{student_name}</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: #94a3b8; font-size: 13px;">Decision</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); color: {status_color}; font-size: 14px; font-weight: 700;">{status_label}</td>
        </tr>
    </table>
    """

    subject = f"Classroom Removal Request {action.capitalize()}: {classroom_name}"
    html_content = _wrap_html_template("Governance Update", f"Removal Request {action.capitalize()}", body_html, cta_url=f"{Config.APP_BASE_URL}/login", cta_text="Go to Portal")

    text_content = f"""
Removal Request Decision:
Classroom: {classroom_name}
Student: {student_name}
Decision: {status_label}
"""
    return _send_email_payload(recipient_email, subject, html_content, text_content, log_tag="REMOVAL DECISION NOTICE")

# -----------------------------------------------------------------------------
# 6. Forgot Password Reset Link Email
# -----------------------------------------------------------------------------
def send_password_reset_token_email(recipient_email, recipient_name, reset_url):
    """Sends password reset link with time-limited token."""
    if not recipient_email:
        return False, "No recipient email"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Hello <strong>{recipient_name}</strong>,<br>
        We received a request to reset your password for your <strong>ScoreTracker.io</strong> account. Click the button below to choose a new password:
    </p>
    <div style="background: rgba(59, 130, 246, 0.08); border-left: 3px solid #3b82f6; padding: 12px 16px; border-radius: 8px; margin: 16px 0; font-size: 13px; color: #cbd5e1;">
        <i class="fas fa-clock text-primary"></i> This password reset link is valid for <strong>{Config.PASSWORD_RESET_EXPIRE_MINUTES} minutes</strong> and can only be used once.
    </div>
    <p style="color: #94a3b8; font-size: 12px; margin-top: 16px;">
        If you did not request a password reset, you can safely ignore this email. Your current password remains unchanged.
    </p>
    """

    subject = "ScoreTracker.io — Password Reset Request"
    html_content = _wrap_html_template("Password Reset", "Reset Your Password", body_html, cta_url=reset_url, cta_text="Reset Password")

    text_content = f"""
Password Reset Request

Hi {recipient_name},
Click the link below to reset your ScoreTracker password (valid for {Config.PASSWORD_RESET_EXPIRE_MINUTES} minutes):
{reset_url}

If you did not request this, you can safely ignore this email.
"""
    return _send_email_payload(recipient_email, subject, html_content, text_content, log_tag="PASSWORD RESET LINK")

# -----------------------------------------------------------------------------
# 7. Password Reset Confirmed Notification
# -----------------------------------------------------------------------------
def send_password_reset_notification_email(user_email, user_name):
    """Sends notification to user confirming their password was changed."""
    if not user_email:
        return False, "No user email"

    login_url = f"{Config.APP_BASE_URL}/login"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Hello <strong>{user_name}</strong>,<br>
        Your <strong>ScoreTracker.io</strong> portal account password has been successfully updated.
    </p>
    <div style="background: rgba(239, 68, 68, 0.08); border-left: 3px solid #ef4444; padding: 14px 18px; border-radius: 8px; margin: 16px 0; font-size: 13px; color: #cbd5e1;">
        <strong>Security Notice:</strong> If you did not make this change, please contact your portal administrator immediately.
    </div>
    """

    subject = "ScoreTracker.io — Account Password Updated"
    html_content = _wrap_html_template("Security Notice", "Password Successfully Updated", body_html, cta_url=login_url, cta_text="Sign In")

    text_content = f"""
Password Updated Successfully

Hello {user_name},
Your ScoreTracker.io account password has been changed.
If you did not make this change, contact your administrator immediately.
"""
    return _send_email_payload(user_email, subject, html_content, text_content, log_tag="PASSWORD RESET NOTICE")

# -----------------------------------------------------------------------------
# 8. Account Status Notification (Activated / Deactivated)
# -----------------------------------------------------------------------------
def send_account_status_notification_email(user_email, user_name, is_active):
    """Sends notification to user when their account is activated or deactivated."""
    if not user_email:
        return False, "No user email"

    status_str = "Activated" if is_active else "Deactivated"
    color = "#10b981" if is_active else "#ef4444"

    body_html = f"""
    <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5; margin-bottom: 16px;">
        Hello <strong>{user_name}</strong>, your account status on the Global Platform Score Tracker has been <strong style="color: {color};">{status_str.lower()}</strong> by an administrator.
    </p>
    """

    subject = f"ScoreTracker.io Account Status Notice: {status_str}"
    html_content = _wrap_html_template("Account Notice", f"Account {status_str}", body_html, cta_url=f"{Config.APP_BASE_URL}/login", cta_text="Portal Login")

    text_content = f"""
Account Status Notice:
Hello {user_name},
Your account status has been updated to: {status_str}
"""
    return _send_email_payload(user_email, subject, html_content, text_content, log_tag="ACCOUNT STATUS NOTICE")
