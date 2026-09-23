import pandas as pd
import os
import uuid
from datetime import datetime, timezone, timedelta, date
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from config import Config

def ensure_export_dir():
    os.makedirs(Config.EXCEL_EXPORT_DIR, exist_ok=True)
    # Clean up export files older than 2 hours
    try:
        now = datetime.now()
        for fname in os.listdir(Config.EXCEL_EXPORT_DIR):
            fpath = os.path.join(Config.EXCEL_EXPORT_DIR, fname)
            if os.path.isfile(fpath) and fname.endswith('.xlsx'):
                file_age = now - datetime.fromtimestamp(os.path.getmtime(fpath))
                if file_age > timedelta(hours=2):
                    try: os.remove(fpath)
                    except: pass
    except: pass

def format_date_safe(val, fmt="%Y-%m-%d %H:%M:%S"):
    if not val:
        return "Never"
    if isinstance(val, (datetime, date)):
        return val.strftime(fmt)
    return str(val)

def style_excel_sheet(ws, title_text="Score Tracker Export"):
    """
    Applies clean, high-contrast corporate styling to an Excel worksheet.
    """
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    alt_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    regular_font = Font(name="Segoe UI", size=9)
    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # Style header row
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
    ws.row_dimensions[1].height = 26

    # Style data rows
    for row_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
        ws.row_dimensions[row_idx].height = 20
        is_even = (row_idx % 2 == 0)
        for cell in row:
            cell.font = regular_font
            cell.border = thin_border
            if is_even:
                cell.fill = alt_fill
            if isinstance(cell.value, (int, float)):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Column autosize
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

def generate_classroom_excel(classroom, students_with_profiles):
    """
    Generates a unique, thread-safe Excel spreadsheet for a classroom roster and performance.
    """
    ensure_export_dir()
    file_id = str(uuid.uuid4())[:8]
    sanitized_name = "".join(c for c in classroom.name if c.isalnum() or c in (' ', '_', '-')).strip()
    filename = f"{sanitized_name}_{file_id}.xlsx"
    file_path = os.path.join(Config.EXCEL_EXPORT_DIR, filename)

    rows = []
    for item in students_with_profiles:
        student = item['student']
        profiles = item['profiles']
        
        if not profiles:
            rows.append({
                "Student Name": student.full_name or student.username,
                "Student ID": student.student_identifier or "N/A",
                "Platform": "None",
                "Handle": "N/A",
                "Rating": 0,
                "Rank": "Unrated",
                "Global Rank": "N/A",
                "Country Rank": "N/A",
                "Solved Problems": 0,
                "Total Contests": 0,
                "Profile URL": "N/A",
                "Last Updated": "N/A"
            })
        else:
            for p in profiles:
                updated_str = format_date_safe(p.last_synced_at, "%Y-%m-%d %H:%M")
                rows.append({
                    "Student Name": student.full_name or student.username,
                    "Student ID": student.student_identifier or "N/A",
                    "Platform": p.platform,
                    "Handle": p.handle,
                    "Rating": p.rating,
                    "Rank": p.rank,
                    "Global Rank": p.global_rank if p.global_rank > 0 else "N/A",
                    "Country Rank": p.country_rank if p.country_rank > 0 else "N/A",
                    "Solved Problems": p.recent_problems,
                    "Total Contests": p.total_contests,
                    "Profile URL": p.profile_url,
                    "Last Updated": updated_str
                })

    df = pd.DataFrame(rows)

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        if not df.empty:
            df.to_excel(writer, sheet_name="Classroom Roster", index=False)
            # Add tabs per platform
            for plat, group in df.groupby("Platform"):
                if plat != "None":
                    sheet_name = str(plat)[:31]
                    group.to_excel(writer, sheet_name=sheet_name, index=False)
        else:
            empty_df = pd.DataFrame(columns=[
                "Student Name", "Student ID", "Platform", "Handle", "Rating",
                "Rank", "Global Rank", "Country Rank", "Solved Problems", "Total Contests", "Profile URL", "Last Updated"
            ])
            empty_df.to_excel(writer, sheet_name="Classroom Roster", index=False)

    wb = openpyxl.load_workbook(file_path)
    for ws in wb.worksheets:
        style_excel_sheet(ws)
    wb.save(file_path)
    return file_path, filename

def generate_student_excel(student, profiles, snapshots):
    """
    Generates a unique Excel file for an individual student's complete performance history.
    """
    ensure_export_dir()
    file_id = str(uuid.uuid4())[:8]
    sanitized_name = "".join(c for c in (student.username or student.email) if c.isalnum() or c in (' ', '_', '-')).strip()
    filename = f"{sanitized_name}_performance_{file_id}.xlsx"
    file_path = os.path.join(Config.EXCEL_EXPORT_DIR, filename)

    # Current Profiles
    p_rows = []
    for p in profiles:
        p_rows.append({
            "Platform": p.platform,
            "Handle": p.handle,
            "Rating": p.rating,
            "Rank": p.rank,
            "Global Rank": p.global_rank if p.global_rank > 0 else "N/A",
            "Country Rank": p.country_rank if p.country_rank > 0 else "N/A",
            "Problems Solved": p.recent_problems,
            "Total Contests": p.total_contests,
            "Profile URL": p.profile_url,
            "Last Synced": format_date_safe(p.last_synced_at)
        })
    df_profiles = pd.DataFrame(p_rows)

    # Historical Snapshots
    s_rows = []
    for s in snapshots:
        s_rows.append({
            "Date": format_date_safe(s.snapshot_date, "%Y-%m-%d"),
            "Platform": s.platform,
            "Rating": s.rating,
            "Rating Delta": s.rating_delta,
            "Rank": s.rank,
            "Problems Solved": s.problems_solved,
            "Problems Delta": s.problems_solved_delta,
            "Contests": s.contests,
            "Contests Delta": s.contests_delta,
            "Calculated Score": s.calculated_score
        })
    df_snapshots = pd.DataFrame(s_rows)

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df_profiles.to_excel(writer, sheet_name="Current Profiles", index=False)
        df_snapshots.to_excel(writer, sheet_name="Daily Snapshots", index=False)

    wb = openpyxl.load_workbook(file_path)
    for ws in wb.worksheets:
        style_excel_sheet(ws)
    wb.save(file_path)
    return file_path, filename

def generate_user_directory_excel(users):
    """
    Generates a professionally styled Excel file containing all user accounts,
    authentication metadata, roles, and status for Administrative Governance.
    """
    ensure_export_dir()
    file_id = str(uuid.uuid4())[:8]
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"User_Directory_Export_{timestamp_str}_{file_id}.xlsx"
    file_path = os.path.join(Config.EXCEL_EXPORT_DIR, filename)

    rows = []
    for idx, u in enumerate(users, start=1):
        rows.append({
            "S.No": idx,
            "User ID": u.id,
            "Full Name": u.full_name or u.username,
            "Username": u.username,
            "Login Email": u.email,
            "Role": u.role.capitalize(),
            "Account Status": "Active" if u.is_active else "Deactivated",
            "Email Verified": "Yes" if u.is_email_verified else "No",
            "Must Change Password": "Yes" if u.must_change_password else "No",
            "Student / Roll ID": u.student_identifier or "N/A",
            "Account Source": getattr(u, 'account_source', 'admin_created'),
            "Password Protection": "Encrypted (scrypt/pbkdf2)",
            "Registered On": format_date_safe(u.created_at),
            "Last Login": format_date_safe(u.last_login_at)
        })

    df = pd.DataFrame(rows)
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="User Accounts", index=False)

    wb = openpyxl.load_workbook(file_path)
    for ws in wb.worksheets:
        style_excel_sheet(ws)
    wb.save(file_path)
    return file_path, filename
