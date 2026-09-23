import os
import re
import io
import pandas as pd
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash
from models_mongo import (
    User, Classroom, ClassroomMembership, BulkImport,
    AuditLog, normalize_email, utc_now
)
from config import Config
from email_service import send_welcome_email, send_classroom_enrolled_email

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

def is_valid_email(email):
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_REGEX.match(email.strip()))

def parse_bulk_file(file_storage, staff_id, classroom_id):
    """
    Parses an uploaded file (.xlsx, .csv, .txt), validates all rows against the database,
    and stages the parsed result in the bulk_imports collection for staff preview.
    Returns (dict_data, error_string).
    """
    filename = file_storage.filename or "upload"
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    
    if ext not in ('xlsx', 'csv', 'txt', 'xls'):
        return None, "Unsupported file format. Please upload an .xlsx, .csv, or .txt file."

    file_bytes = file_storage.read()
    if len(file_bytes) > 5 * 1024 * 1024:  # 5MB limit
        return None, "File size exceeds 5MB limit."

    raw_records = []

    try:
        if ext in ('xlsx', 'xls'):
            df = pd.read_excel(io.BytesIO(file_bytes))
            raw_records = _extract_from_dataframe(df)
        elif ext == 'csv':
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8')
            except Exception:
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='latin-1')
            raw_records = _extract_from_dataframe(df)
        elif ext == 'txt':
            text = file_bytes.decode('utf-8', errors='ignore')
            lines = text.splitlines()
            for line in lines:
                line = line.strip()
                if line:
                    raw_records.append({'email': line, 'full_name': '', 'student_identifier': ''})
    except Exception as e:
        return None, f"Failed to parse file contents: {str(e)}"

    if not raw_records:
        return None, "The uploaded file contains no data or could not be parsed."

    if len(raw_records) > 2000:
        return None, "File exceeds the maximum limit of 2,000 rows per batch."

    # Validate each record and detect duplicates / roles
    seen_in_file = set()
    staged_rows = []
    summary = {
        'total_rows': len(raw_records),
        'new_accounts': 0,
        'existing_students': 0,
        'already_enrolled': 0,
        'invalid_emails': 0,
        'wrong_roles': 0,
        'duplicates_in_file': 0,
        'processable_count': 0
    }

    for idx, r in enumerate(raw_records, start=1):
        raw_email = (r.get('email') or '').strip()
        norm_email = normalize_email(raw_email)
        name = (r.get('full_name') or '').strip()
        identifier = (r.get('student_identifier') or '').strip()

        row_entry = {
            'row_num': idx,
            'email': raw_email,
            'email_normalized': norm_email,
            'full_name': name,
            'student_identifier': identifier,
            'status': '',
            'status_label': '',
            'description': ''
        }

        if not is_valid_email(raw_email):
            row_entry['status'] = 'INVALID_EMAIL'
            row_entry['status_label'] = 'Invalid Email'
            row_entry['description'] = 'Email syntax is malformed'
            summary['invalid_emails'] += 1
            staged_rows.append(row_entry)
            continue

        if norm_email in seen_in_file:
            row_entry['status'] = 'DUPLICATE_IN_FILE'
            row_entry['status_label'] = 'Duplicate in File'
            row_entry['description'] = 'This email appears multiple times in the upload'
            summary['duplicates_in_file'] += 1
            staged_rows.append(row_entry)
            continue

        seen_in_file.add(norm_email)

        # Check existing user in database
        existing_user = User.find_by_email(norm_email)
        if existing_user:
            if existing_user.role != 'student':
                row_entry['status'] = 'WRONG_ROLE'
                row_entry['status_label'] = 'Wrong Role'
                row_entry['description'] = f"Account already registered as {existing_user.role.title()}"
                summary['wrong_roles'] += 1
            else:
                # Check classroom membership
                membership = ClassroomMembership.find_one(classroom_id, existing_user.id)
                if membership and membership.status == 'active':
                    row_entry['status'] = 'ALREADY_ENROLLED'
                    row_entry['status_label'] = 'Already Enrolled'
                    row_entry['description'] = 'Student is already an active member of this classroom'
                    summary['already_enrolled'] += 1
                else:
                    row_entry['status'] = 'EXISTING_STUDENT'
                    row_entry['status_label'] = 'Existing Student'
                    row_entry['description'] = 'Existing account will be enrolled (password unchanged)'
                    summary['existing_students'] += 1
                    summary['processable_count'] += 1
        else:
            row_entry['status'] = 'NEW_ACCOUNT'
            row_entry['status_label'] = 'New Account'
            row_entry['description'] = f"New student account with default password '{Config.DEFAULT_STUDENT_PASSWORD}'"
            summary['new_accounts'] += 1
            summary['processable_count'] += 1

        staged_rows.append(row_entry)

    # Save to staging collection
    staging = BulkImport.create_staging(
        staff_id=staff_id,
        classroom_id=classroom_id,
        filename=filename,
        file_type=ext,
        rows=staged_rows,
        summary=summary
    )

    return {
        'import_id': staging.import_id,
        'summary': summary,
        'rows': staged_rows
    }, None

def parse_and_stage_bulk_file(file_storage, staff_id, classroom_id):
    res, err = parse_bulk_file(file_storage, staff_id, classroom_id)
    if err:
        return None, err
    return BulkImport.find_by_import_id(res['import_id']), None

def _extract_from_dataframe(df):
    """Detects and extracts email, name, and student_id columns from pandas DataFrame."""
    records = []
    col_map = {}
    for col in df.columns:
        c_clean = str(col).strip().lower().replace(' ', '_').replace('-', '_')
        if c_clean in ('email', 'mail', 'student_email', 'email_address', 'e_mail'):
            col_map['email'] = col
        elif c_clean in ('name', 'full_name', 'student_name', 'student'):
            col_map['full_name'] = col
        elif c_clean in ('student_id', 'roll_no', 'roll_number', 'identifier', 'id_number', 'reg_no'):
            col_map['student_identifier'] = col

    if 'email' not in col_map:
        for col in df.columns:
            sample = df[col].dropna().astype(str).head(5)
            if any('@' in val and '.' in val for val in sample):
                col_map['email'] = col
                break

    if 'email' not in col_map:
        if len(df.columns) > 0:
            col_map['email'] = df.columns[0]

    for _, row in df.iterrows():
        email_val = str(row.get(col_map.get('email'), '')).strip()
        if email_val and email_val.lower() != 'nan':
            name_val = str(row.get(col_map.get('full_name'), '')).strip() if 'full_name' in col_map else ''
            if name_val.lower() == 'nan':
                name_val = ''
            id_val = str(row.get(col_map.get('student_identifier'), '')).strip() if 'student_identifier' in col_map else ''
            if id_val.lower() == 'nan':
                id_val = ''
            records.append({
                'email': email_val,
                'full_name': name_val,
                'student_identifier': id_val
            })

    return records

def execute_bulk_enrollment(staging_doc, staff, classroom):
    """
    Executes staged records:
    1. Provisions new student accounts with default password Student@123
    2. Enrolls existing students (passwords untouched)
    3. Dispatches welcome/enrollment emails
    4. Generates Excel summary result
    """
    import_id = staging_doc.import_id
    BulkImport.update_status(import_id, 'processing')

    processed_rows = []
    created_count = 0
    enrolled_count = 0
    emails_sent = 0
    emails_failed = 0

    login_url = f"{Config.APP_BASE_URL.rstrip('/')}/login/student"

    for r in staging_doc.rows:
        status = r.get('status')
        norm_email = r.get('email_normalized') or normalize_email(r.get('email'))
        raw_email = r.get('email')
        name = r.get('full_name') or ''
        identifier = r.get('student_identifier') or ''
        action_taken = ""

        if status == 'NEW_ACCOUNT':
            # Create student user
            uname_base = norm_email.split('@')[0]
            candidate_uname = uname_base
            collision = 1
            while User.find_by_username(candidate_uname):
                collision += 1
                candidate_uname = f"{uname_base}_{collision}"

            student = User.create({
                'email': raw_email,
                'username': candidate_uname,
                'full_name': name or candidate_uname,
                'student_identifier': identifier or None,
                'password_hash': generate_password_hash(Config.DEFAULT_STUDENT_PASSWORD),
                'role': 'student',
                'is_active': True,
                'is_email_verified': True,
                'must_change_password': True,
                'account_source': 'bulk_import'
            })
            # Enroll
            ClassroomMembership.create_or_activate(classroom.id, student.id)
            created_count += 1
            action_taken = f"Account created with initial password '{Config.DEFAULT_STUDENT_PASSWORD}' and enrolled"

            # Dispatch Welcome Email
            try:
                success, _ = send_welcome_email(
                    recipient_email=raw_email,
                    recipient_name=name or candidate_uname,
                    username=candidate_uname,
                    role='student',
                    student_identifier=identifier,
                    login_url=login_url,
                    is_newly_provisioned=True,
                    default_password=Config.DEFAULT_STUDENT_PASSWORD
                )
                if success:
                    emails_sent += 1
                else:
                    emails_failed += 1
            except Exception:
                emails_failed += 1

        elif status == 'EXISTING_STUDENT':
            student = User.find_by_email(norm_email)
            if student:
                ClassroomMembership.create_or_activate(classroom.id, student.id)
                enrolled_count += 1
                action_taken = "Enrolled existing student (credentials unchanged)"

                # Dispatch Enrollment Notice
                try:
                    success, _ = send_classroom_enrolled_email(
                        student_email=student.email,
                        student_name=student.full_name or student.username,
                        classroom_name=classroom.name,
                        staff_name=staff.full_name or staff.username,
                        classroom_url=f"{Config.APP_BASE_URL.rstrip('/')}/student/classroom/{classroom.id}",
                        is_new_student=False
                    )
                    if success:
                        emails_sent += 1
                    else:
                        emails_failed += 1
                except Exception:
                    emails_failed += 1
        elif status == 'ALREADY_ENROLLED':
            action_taken = "Skipped (already enrolled in this classroom)"
        elif status == 'WRONG_ROLE':
            action_taken = "Skipped (account belongs to faculty/admin)"
        elif status == 'INVALID_EMAIL':
            action_taken = "Skipped (invalid email format)"
        elif status == 'DUPLICATE_IN_FILE':
            action_taken = "Skipped (duplicate row in upload)"
        else:
            action_taken = "Skipped"

        processed_rows.append({
            'row_num': r.get('row_num'),
            'email': raw_email,
            'full_name': name,
            'student_identifier': identifier,
            'status': status,
            'status_label': r.get('status_label'),
            'action_taken': action_taken
        })

    completion_summary = {
        'total_rows': len(staging_doc.rows),
        'new_accounts_created': created_count,
        'existing_enrolled': enrolled_count,
        'already_enrolled': staging_doc.summary.get('already_enrolled', 0),
        'invalid_emails': staging_doc.summary.get('invalid_emails', 0),
        'duplicates_in_file': staging_doc.summary.get('duplicates_in_file', 0),
        'wrong_roles': staging_doc.summary.get('wrong_roles', 0),
        'emails_sent': emails_sent,
        'emails_failed': emails_failed
    }

    BulkImport.update_status(
        import_id=import_id,
        status='completed',
        additional_fields={
            'confirmed_at': utc_now(),
            'completed_at': utc_now(),
            'rows': processed_rows,
            'summary': completion_summary
        }
    )

    excel_bytes = generate_bulk_result_excel(staging_doc, classroom.name, processed_rows=processed_rows)
    return completion_summary, excel_bytes

def confirm_and_process_bulk_import(import_id, staff_id, classroom_id):
    staged = BulkImport.find_by_import_id(import_id)
    if not staged:
        return None, "Import session expired or not found."
    staff = User.find_by_id(staff_id)
    classroom = Classroom.find_by_id(classroom_id)
    summary, _ = execute_bulk_enrollment(staged, staff, classroom)
    return summary, None

def generate_bulk_result_excel(staging_doc, classroom_name="Classroom", processed_rows=None):
    """Generates an Excel workbook byte array containing the results of a bulk import."""
    rows_to_export = processed_rows or staging_doc.rows or []
    data = []
    for r in rows_to_export:
        data.append({
            "Row #": r.get('row_num'),
            "Email Address": r.get('email'),
            "Full Name": r.get('full_name', ''),
            "Student ID / Roll No": r.get('student_identifier', ''),
            "Status": r.get('status_label', r.get('status')),
            "Action Taken": r.get('action_taken', r.get('description', ''))
        })

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Import Results')
    output.seek(0)
    return output.getvalue()

def generate_bulk_import_result_excel(import_id):
    staged = BulkImport.find_by_import_id(import_id)
    if not staged:
        return None
    return io.BytesIO(generate_bulk_result_excel(staged))
