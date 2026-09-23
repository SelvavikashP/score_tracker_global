"""
Database Migration Utility: SQLite / PostgreSQL -> MongoDB Atlas
Preserves all user identities, classrooms, memberships, platform metrics,
historical snapshots, audit logs, and removal requests.
"""

import sys
import os
import argparse
import sqlite3
from datetime import datetime, timezone
from bson.objectid import ObjectId

# Ensure app root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import Config
from mongo_db import (
    get_db, get_users_col, get_classrooms_col, get_memberships_col,
    get_profiles_col, get_snapshots_col, get_removal_requests_col,
    get_audit_logs_col, init_indexes
)
from models_mongo import normalize_email
from encryption_utils import decrypt_field

def parse_datetime(dt_val):
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val
    try:
        # Try standard ISO or string formats
        return datetime.fromisoformat(str(dt_val).replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(str(dt_val)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

def migrate_from_sqlite(sqlite_path):
    print(f"Connecting to SQLite database: {sqlite_path}")
    if not os.path.exists(sqlite_path):
        print(f"Error: SQLite database file not found at {sqlite_path}")
        return False

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Ensure MongoDB indexes
    print("Ensuring MongoDB Atlas indexes...")
    init_indexes()

    users_col = get_users_col()
    classrooms_col = get_classrooms_col()
    memberships_col = get_memberships_col()
    profiles_col = get_profiles_col()
    snapshots_col = get_snapshots_col()
    removal_col = get_removal_requests_col()
    audit_col = get_audit_logs_col()

    user_id_map = {}       # legacy_id -> ObjectId
    classroom_id_map = {}  # legacy_id -> ObjectId

    summary = {
        'users': 0,
        'classrooms': 0,
        'memberships': 0,
        'profiles': 0,
        'snapshots': 0,
        'removal_requests': 0,
        'audit_logs': 0,
        'errors': 0
    }

    # 1. Migrate Users
    print("\n--- Migrating Users ---")
    try:
        cursor.execute("SELECT * FROM user")
        user_rows = cursor.fetchall()
        for row in user_rows:
            try:
                r = dict(row)
                legacy_id = r.get('id')
                raw_email = r.get('email', '').strip()
                norm_email = normalize_email(raw_email)
                
                # Check for existing user in MongoDB
                existing = users_col.find_one({'email_normalized': norm_email})
                if existing:
                    user_id_map[legacy_id] = existing['_id']
                    print(f"  [EXISTS] User {norm_email} already in MongoDB (mapped ID {legacy_id} -> {existing['_id']})")
                    summary['users'] += 1
                    continue

                full_name = decrypt_field(r.get('full_name'))
                student_id = decrypt_field(r.get('student_identifier'))
                
                user_doc = {
                    'email': raw_email,
                    'email_normalized': norm_email,
                    'username': r.get('username', norm_email.split('@')[0]),
                    'full_name': full_name or r.get('username', ''),
                    'student_identifier': student_id,
                    'password_hash': r.get('password_hash'),
                    'role': r.get('role', 'student'),
                    'is_active': bool(r.get('is_active', True)),
                    'is_email_verified': bool(r.get('is_email_verified', True)),
                    'must_change_password': False,  # Existing migrated accounts keep current passwords
                    'account_source': 'migrated',
                    'created_at': parse_datetime(r.get('created_at')),
                    'updated_at': datetime.now(timezone.utc),
                    'last_login_at': parse_datetime(r.get('last_login_at')),
                    'password_changed_at': parse_datetime(r.get('created_at')),
                    'deactivated_at': None,
                    'legacy_id': legacy_id
                }
                res = users_col.insert_one(user_doc)
                user_id_map[legacy_id] = res.inserted_id
                summary['users'] += 1
                print(f"  [MIGRATED] User: {norm_email} ({user_doc['role']})")
            except Exception as e:
                print(f"  [ERROR] Failed to migrate user {row}: {e}")
                summary['errors'] += 1
    except Exception as e:
        print(f"Error querying users from SQLite: {e}")
        summary['errors'] += 1

    # 2. Migrate Classrooms
    print("\n--- Migrating Classrooms ---")
    try:
        cursor.execute("SELECT * FROM classroom")
        class_rows = cursor.fetchall()
        for row in class_rows:
            try:
                r = dict(row)
                legacy_id = r.get('id')
                legacy_staff_id = r.get('staff_id')
                mongo_staff_id = user_id_map.get(legacy_staff_id)

                if not mongo_staff_id:
                    print(f"  [SKIP] Classroom '{r.get('name')}' - staff ID {legacy_staff_id} not mapped.")
                    continue

                class_doc = {
                    'name': r.get('name', '').strip(),
                    'description': r.get('description', '') or '',
                    'staff_id': str(mongo_staff_id),
                    'status': r.get('status', 'active'),
                    'created_at': parse_datetime(r.get('created_at')),
                    'updated_at': datetime.now(timezone.utc),
                    'archived_at': None,
                    'legacy_id': legacy_id
                }
                res = classrooms_col.insert_one(class_doc)
                classroom_id_map[legacy_id] = res.inserted_id
                summary['classrooms'] += 1
                print(f"  [MIGRATED] Classroom: '{class_doc['name']}'")
            except Exception as e:
                print(f"  [ERROR] Failed to migrate classroom {row}: {e}")
                summary['errors'] += 1
    except Exception as e:
        print(f"Error querying classrooms from SQLite: {e}")
        summary['errors'] += 1

    # 3. Migrate Classroom Memberships
    print("\n--- Migrating Classroom Memberships ---")
    try:
        cursor.execute("SELECT * FROM classroom_membership")
        mem_rows = cursor.fetchall()
        for row in mem_rows:
            try:
                r = dict(row)
                c_id = classroom_id_map.get(r.get('classroom_id'))
                s_id = user_id_map.get(r.get('student_id'))

                if not c_id or not s_id:
                    continue

                mem_doc = {
                    'classroom_id': str(c_id),
                    'student_id': str(s_id),
                    'status': r.get('status', 'active'),
                    'joined_at': parse_datetime(r.get('joined_at')),
                    'updated_at': datetime.now(timezone.utc),
                    'removed_at': None
                }
                memberships_col.update_one(
                    {'classroom_id': mem_doc['classroom_id'], 'student_id': mem_doc['student_id']},
                    {'$set': mem_doc},
                    upsert=True
                )
                summary['memberships'] += 1
            except Exception as e:
                print(f"  [ERROR] Failed to migrate membership {row}: {e}")
                summary['errors'] += 1
    except Exception as e:
        print(f"Error querying memberships: {e}")
        summary['errors'] += 1

    # 4. Migrate Platform Profiles
    print("\n--- Migrating Platform Profiles ---")
    try:
        cursor.execute("SELECT * FROM platform_profile")
        prof_rows = cursor.fetchall()
        for row in prof_rows:
            try:
                r = dict(row)
                u_id = user_id_map.get(r.get('user_id'))
                if not u_id:
                    continue

                prof_doc = {
                    'user_id': str(u_id),
                    'platform': r.get('platform'),
                    'handle': r.get('handle', ''),
                    'profile_url': r.get('profile_url', ''),
                    'rating': int(r.get('rating', 0) or 0),
                    'rank': r.get('rank', 'Unrated'),
                    'global_rank': int(r.get('global_rank', 0) or 0),
                    'country_rank': int(r.get('country_rank', 0) or 0),
                    'recent_problems': int(r.get('recent_problems', 0) or 0),
                    'total_contests': int(r.get('total_contests', 0) or 0),
                    'last_synced_at': parse_datetime(r.get('last_synced_at')),
                    'sync_status': r.get('sync_status', 'pending'),
                    'sync_error': r.get('sync_error')
                }
                profiles_col.update_one(
                    {'user_id': prof_doc['user_id'], 'platform': prof_doc['platform']},
                    {'$set': prof_doc},
                    upsert=True
                )
                summary['profiles'] += 1
            except Exception as e:
                print(f"  [ERROR] Failed to migrate profile {row}: {e}")
                summary['errors'] += 1
    except Exception as e:
        print(f"Error querying profiles: {e}")
        summary['errors'] += 1

    # 5. Migrate Performance Snapshots
    print("\n--- Migrating Performance Snapshots ---")
    try:
        cursor.execute("SELECT * FROM performance_snapshot")
        snap_rows = cursor.fetchall()
        for row in snap_rows:
            try:
                r = dict(row)
                u_id = user_id_map.get(r.get('user_id'))
                if not u_id:
                    continue

                snap_date = str(r.get('snapshot_date') or datetime.now(timezone.utc).strftime('%Y-%m-%d'))
                snap_doc = {
                    'user_id': str(u_id),
                    'platform': r.get('platform'),
                    'snapshot_date': snap_date,
                    'rating': int(r.get('rating', 0) or 0),
                    'rating_delta': int(r.get('rating_delta', 0) or 0),
                    'rank': r.get('rank', 'Unrated'),
                    'global_rank': int(r.get('global_rank', 0) or 0),
                    'country_rank': int(r.get('country_rank', 0) or 0),
                    'problems_solved': int(r.get('problems_solved', 0) or 0),
                    'problems_solved_delta': int(r.get('problems_solved_delta', 0) or 0),
                    'contests': int(r.get('contests', 0) or 0),
                    'contests_delta': int(r.get('contests_delta', 0) or 0),
                    'calculated_score': float(r.get('calculated_score', 0.0) or 0.0),
                    'created_at': parse_datetime(r.get('created_at'))
                }
                snapshots_col.update_one(
                    {'user_id': snap_doc['user_id'], 'platform': snap_doc['platform'], 'snapshot_date': snap_doc['snapshot_date']},
                    {'$set': snap_doc},
                    upsert=True
                )
                summary['snapshots'] += 1
            except Exception as e:
                print(f"  [ERROR] Failed to migrate snapshot {row}: {e}")
                summary['errors'] += 1
    except Exception as e:
        print(f"Error querying snapshots: {e}")
        summary['errors'] += 1

    # 6. Migrate Removal Requests
    print("\n--- Migrating Removal Requests ---")
    try:
        cursor.execute("SELECT * FROM removal_request")
        rem_rows = cursor.fetchall()
        for row in rem_rows:
            try:
                r = dict(row)
                staff_id = user_id_map.get(r.get('staff_id'))
                student_id = user_id_map.get(r.get('student_id'))
                classroom_id = classroom_id_map.get(r.get('classroom_id'))
                reviewer_id = user_id_map.get(r.get('reviewed_by_id')) if r.get('reviewed_by_id') else None

                if not staff_id or not student_id or not classroom_id:
                    continue

                rem_doc = {
                    'staff_id': str(staff_id),
                    'student_id': str(student_id),
                    'classroom_id': str(classroom_id),
                    'reason': decrypt_field(r.get('reason')) or '',
                    'status': r.get('status', 'pending'),
                    'reviewed_by_id': str(reviewer_id) if reviewer_id else None,
                    'reviewed_at': parse_datetime(r.get('reviewed_at')),
                    'admin_notes': decrypt_field(r.get('admin_notes')),
                    'created_at': parse_datetime(r.get('created_at')),
                    'updated_at': datetime.now(timezone.utc)
                }
                removal_col.insert_one(rem_doc)
                summary['removal_requests'] += 1
            except Exception as e:
                print(f"  [ERROR] Failed to migrate removal request {row}: {e}")
                summary['errors'] += 1
    except Exception as e:
        print(f"Error querying removal requests: {e}")
        summary['errors'] += 1

    # 7. Migrate Audit Logs
    print("\n--- Migrating Audit Logs ---")
    try:
        cursor.execute("SELECT * FROM audit_log")
        log_rows = cursor.fetchall()
        for row in log_rows:
            try:
                r = dict(row)
                actor_id = user_id_map.get(r.get('actor_id')) if r.get('actor_id') else None
                log_doc = {
                    'actor_id': str(actor_id) if actor_id else None,
                    'actor_role': None,
                    'event_type': r.get('event_type', 'SYSTEM_EVENT'),
                    'description': r.get('description', ''),
                    'target_type': r.get('target_type'),
                    'target_id': str(r.get('target_id', '')) if r.get('target_id') else None,
                    'result': 'success',
                    'ip_address': r.get('ip_address'),
                    'created_at': parse_datetime(r.get('created_at'))
                }
                audit_col.insert_one(log_doc)
                summary['audit_logs'] += 1
            except Exception as e:
                print(f"  [ERROR] Failed to migrate audit log {row}: {e}")
                summary['errors'] += 1
    except Exception as e:
        print(f"Error querying audit logs: {e}")
        summary['errors'] += 1

    conn.close()

    print("\n=======================================================")
    print("MIGRATION SUMMARY:")
    print(f"  Users migrated:            {summary['users']}")
    print(f"  Classrooms migrated:       {summary['classrooms']}")
    print(f"  Memberships migrated:      {summary['memberships']}")
    print(f"  Platform profiles:         {summary['profiles']}")
    print(f"  Performance snapshots:     {summary['snapshots']}")
    print(f"  Removal requests:          {summary['removal_requests']}")
    print(f"  Audit logs:                {summary['audit_logs']}")
    print(f"  Errors encountered:        {summary['errors']}")
    print("=======================================================\n")
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Migrate SQL database to MongoDB Atlas")
    parser.add_argument('--sqlite', default=os.path.join(BASE_DIR, 'database.db'), help="Path to SQLite database file")
    args = parser.parse_args()

    print("=== STARTING MONGODB MIGRATION ===")
    migrate_from_sqlite(args.sqlite)
    print("=== MIGRATION COMPLETE ===")
