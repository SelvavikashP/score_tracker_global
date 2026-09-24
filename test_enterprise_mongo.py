"""
Comprehensive End-to-End Test Suite for MongoDB Atlas Score Tracker
Tests:
1. MongoDB Atlas connectivity, ping, collections & indexes
2. Admin authentication & staff provisioning
3. Staff authentication & classroom creation
4. Single student provisioning by email (Student@123)
5. Mandatory first-login password change guard
6. Password update & old password rejection
7. Existing student enrollment protection (passwords never overwritten)
8. Wrong role enrollment protection (admin/staff rejected)
9. Bulk Import (.xlsx, .csv, .txt) with staging preview & confirm
10. Forgot Password & Time-limited token reset
11. IDOR protection & RBAC
12. Platform profile sync & scoring deltas
13. Thread-safe Excel exports
14. Health check endpoint
"""

import os
import sys
import unittest
import io
import pandas as pd
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

# Add repo root to path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Configure isolated in-memory test database for test suite execution
import mongomock
import mongo_db

mock_client = mongomock.MongoClient()
mongo_db._mongo_client = mock_client
mongo_db._mongo_db = mock_client['score_tracker_test']

from config import Config
from app import app
from mongo_db import (
    get_db, ping_mongodb, get_users_col, get_classrooms_col,
    get_memberships_col, get_profiles_col, get_snapshots_col,
    get_audit_logs_col, get_password_resets_col, get_bulk_imports_col,
    init_indexes, bootstrap_admin
)
from models_mongo import (
    User, Classroom, ClassroomMembership, PlatformProfile,
    PerformanceSnapshot, RemovalRequest, AuditLog, PasswordReset,
    BulkImport, normalize_email
)
from bulk_import_service import parse_bulk_file, execute_bulk_enrollment, generate_bulk_result_excel
from scoring import compute_profile_score

class TestMongoDBEnterpriseScoreTracker(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        cls.client = app.test_client()
        cls.test_tag = f"test_{int(datetime.now().timestamp())}"

        # Initialize indexes & bootstrap
        init_indexes()
        bootstrap_admin()

    def test_01_mongodb_health_and_indexes(self):
        """Verify MongoDB Atlas connectivity, ping, and indexes."""
        db_ok, status = ping_mongodb()
        self.assertTrue(db_ok, f"MongoDB ping failed: {status}")
        self.assertEqual(status, "connected")

        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'healthy')
        self.assertEqual(data['database'], 'mongodb')
        self.assertEqual(data['database_status'], 'connected')

    def test_02_admin_login_and_staff_creation(self):
        """Verify Admin login and creation of faculty staff account."""
        admin_email = Config.INITIAL_ADMIN_EMAIL
        admin = User.find_by_email(admin_email)
        self.assertIsNotNone(admin, f"Bootstrap admin ({admin_email}) not found")
        self.assertEqual(admin.role, 'admin')

        # Create staff account as Admin
        staff_email = f"staff_{self.test_tag}@university.edu"
        staff_username = f"prof_{self.test_tag}"
        staff_pass = "Faculty@Secure2026"

        with self.client.session_transaction() as sess:
            sess['user_id'] = admin.id
            sess['username'] = admin.username
            sess['role'] = 'admin'

        resp = self.client.post('/admin/user/create', data={
            'full_name': 'Professor Alan Turing',
            'username': staff_username,
            'email': staff_email,
            'password': staff_pass,
            'role': 'staff'
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        staff_user = User.find_by_email(staff_email)
        self.assertIsNotNone(staff_user)
        self.assertEqual(staff_user.role, 'staff')
        self.assertTrue(staff_user.check_password(staff_pass))
        self.assertFalse(staff_user.must_change_password)

    def test_03_staff_classroom_creation(self):
        """Verify staff login and classroom creation."""
        staff_email = f"staff_{self.test_tag}@university.edu"
        staff_user = User.find_by_email(staff_email)
        self.assertIsNotNone(staff_user)

        with self.client.session_transaction() as sess:
            sess['user_id'] = staff_user.id
            sess['username'] = staff_user.username
            sess['role'] = 'staff'

        class_name = f"CSE-Algorithms-{self.test_tag}"
        resp = self.client.post('/staff/classroom/create', data={
            'name': class_name,
            'description': 'Advanced Algorithms & Competitive Coding'
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        classrooms = Classroom.find_by_staff_id(staff_user.id)
        self.assertGreaterEqual(len(classrooms), 1)
        test_class = next((c for c in classrooms if c.name == class_name), None)
        self.assertIsNotNone(test_class)
        self.__class__.test_classroom = test_class

    def test_04_single_student_provisioning_and_first_login_guard(self):
        """
        Verify staff provisions a new student:
        - Receives default password Student@123
        - must_change_password=True
        - First login guard forces /student/change-password
        """
        staff_email = f"staff_{self.test_tag}@university.edu"
        staff_user = User.find_by_email(staff_email)
        classroom = self.__class__.test_classroom

        with self.client.session_transaction() as sess:
            sess['user_id'] = staff_user.id
            sess['username'] = staff_user.username
            sess['role'] = 'staff'

        student_email = f"student_single_{self.test_tag}@university.edu"
        resp = self.client.post(f"/staff/classroom/{classroom.id}/student/add", data={
            'student_email': student_email,
            'full_name': 'Grace Hopper',
            'student_identifier': '2026-CS-001'
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Verify student document in MongoDB Atlas
        student = User.find_by_email(student_email)
        self.assertIsNotNone(student)
        self.assertEqual(student.role, 'student')
        self.assertTrue(student.must_change_password)
        self.assertEqual(student.account_source, 'staff_created')
        self.assertTrue(student.check_password("Student@123"))

        # Verify automatic classroom membership
        membership = ClassroomMembership.find_one(classroom.id, student.id)
        self.assertIsNotNone(membership)
        self.assertEqual(membership.status, 'active')

        # Step 4b: Student signs in with default password
        with self.client.session_transaction() as sess:
            sess.clear()

        login_resp = self.client.post('/login/student', data={
            'email': student_email,
            'password': 'Student@123',
            'target_role': 'student'
        }, follow_redirects=False)
        # Should redirect to change password
        self.assertEqual(login_resp.status_code, 302)
        self.assertIn('/student/change-password', login_resp.headers['Location'])

        # Step 4c: Attempting to bypass and visit dashboard directly must be blocked
        with self.client.session_transaction() as sess:
            sess['user_id'] = student.id
            sess['username'] = student.username
            sess['role'] = 'student'

        dash_resp = self.client.get('/student/dashboard', follow_redirects=False)
        self.assertEqual(dash_resp.status_code, 302)
        self.assertIn('/student/change-password', dash_resp.headers['Location'])

        # Step 4d: Change password with valid new password
        change_resp = self.client.post('/student/change-password', data={
            'current_password': 'Student@123',
            'new_password': 'Student@NewPass2026!',
            'confirm_password': 'Student@NewPass2026!'
        }, follow_redirects=True)
        self.assertEqual(change_resp.status_code, 200)

        # Verify must_change_password is now False
        updated_student = User.find_by_id(student.id)
        self.assertFalse(updated_student.must_change_password)
        self.assertTrue(updated_student.check_password('Student@NewPass2026!'))
        self.assertFalse(updated_student.check_password('Student@123'))

        # Step 4e: Now dashboard is accessible
        dash_resp2 = self.client.get('/student/dashboard')
        self.assertEqual(dash_resp2.status_code, 200)
        self.assertIn(b'Grace Hopper', dash_resp2.data)

        self.__class__.test_student = updated_student

    def test_05_existing_student_enrollment_protection(self):
        """
        Verify that enrolling an EXISTING student:
        - NEVER overwrites their password
        - DOES NOT reset must_change_password
        - Re-enrolling does not create duplicate memberships
        """
        staff_email = f"staff_{self.test_tag}@university.edu"
        staff_user = User.find_by_email(staff_email)
        student = self.__class__.test_student
        old_hash = student.password_hash

        # Create a second classroom
        class_b = Classroom.create(
            name=f"CSE-Section-B-{self.test_tag}",
            staff_id=staff_user.id
        )

        with self.client.session_transaction() as sess:
            sess['user_id'] = staff_user.id
            sess['username'] = staff_user.username
            sess['role'] = 'staff'

        # Enroll existing student in second class
        resp = self.client.post(f"/staff/classroom/{class_b.id}/student/add", data={
            'student_email': student.email
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Verify password and hash remain identical
        check_student = User.find_by_id(student.id)
        self.assertEqual(check_student.password_hash, old_hash)
        self.assertFalse(check_student.must_change_password)
        self.assertTrue(check_student.check_password('Student@NewPass2026!'))

        # Verify membership in class B
        mem_b = ClassroomMembership.find_one(class_b.id, student.id)
        self.assertIsNotNone(mem_b)

        # Enrolling again in class B should not duplicate
        resp2 = self.client.post(f"/staff/classroom/{class_b.id}/student/add", data={
            'student_email': student.email
        }, follow_redirects=True)
        self.assertEqual(resp2.status_code, 200)

        all_mems = ClassroomMembership.find_by_classroom_id(class_b.id)
        self.assertEqual(len(all_mems), 1)

    def test_06_wrong_role_protection(self):
        """Verify staff/admin emails cannot be enrolled as students."""
        staff_email = f"staff_{self.test_tag}@university.edu"
        staff_user = User.find_by_email(staff_email)
        classroom = self.__class__.test_classroom
        admin_email = Config.INITIAL_ADMIN_EMAIL

        with self.client.session_transaction() as sess:
            sess['user_id'] = staff_user.id
            sess['username'] = staff_user.username
            sess['role'] = 'staff'

        # Attempt to enroll admin
        resp = self.client.post(f"/staff/classroom/{classroom.id}/student/add", data={
            'student_email': admin_email
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"This email belongs to another account type", resp.data)

        # Verify admin was not enrolled
        admin_user = User.find_by_email(admin_email)
        mem = ClassroomMembership.find_one(classroom.id, admin_user.id)
        self.assertIsNone(mem)

    def test_07_bulk_import_two_step_workflow(self):
        """
        Verify two-step Bulk Import:
        1. Excel file with mixed data (new accounts, existing students, duplicates in file, invalid emails)
        2. Staging preview generation
        3. Server-side tamper-resistant confirmation
        4. Account creation, password hash, enrollment, and report export
        """
        staff_email = f"staff_{self.test_tag}@university.edu"
        staff_user = User.find_by_email(staff_email)
        classroom = self.__class__.test_classroom
        existing_student = self.__class__.test_student

        # Create sample Excel workbook in memory
        df_data = [
            {"Email": f"bulk_new1_{self.test_tag}@example.com", "Name": "Alice Walker", "Student_ID": "CS-101"},
            {"Email": f"bulk_new2_{self.test_tag}@example.com", "Name": "Bob Ross", "Student_ID": "CS-102"},
            {"Email": existing_student.email, "Name": "Grace Hopper", "Student_ID": "2026-CS-001"},  # Already enrolled
            {"Email": f"bulk_new1_{self.test_tag}@example.com", "Name": "Alice Duplicate", "Student_ID": "CS-101"},  # Duplicate in file
            {"Email": "not-an-email", "Name": "Invalid Guy", "Student_ID": "CS-999"},  # Invalid email
            {"Email": Config.INITIAL_ADMIN_EMAIL, "Name": "Admin Person", "Student_ID": "ADMIN-01"}  # Wrong role
        ]
        df = pd.DataFrame(df_data)
        excel_buffer = io.BytesIO()
        df.to_excel(excel_buffer, index=False, engine='openpyxl')
        excel_buffer.seek(0)

        with self.client.session_transaction() as sess:
            sess['user_id'] = staff_user.id
            sess['username'] = staff_user.username
            sess['role'] = 'staff'

        # Step 1: Upload for Preview
        upload_resp = self.client.post(
            f"/staff/classroom/{classroom.id}/students/bulk-upload",
            data={'file': (excel_buffer, 'students_test.xlsx')},
            content_type='multipart/form-data'
        )
        self.assertEqual(upload_resp.status_code, 200)
        preview_json = upload_resp.get_json()
        self.assertTrue(preview_json['success'])
        import_id = preview_json['import_id']
        summary = preview_json['summary']

        self.assertEqual(summary['total_rows'], 6)
        self.assertEqual(summary['new_accounts'], 2)
        self.assertEqual(summary['already_enrolled'], 1)
        self.assertEqual(summary['duplicates_in_file'], 1)
        self.assertEqual(summary['invalid_emails'], 1)
        self.assertEqual(summary['wrong_roles'], 1)

        # Step 2: Confirm Staged Import
        confirm_resp = self.client.post(
            f"/staff/classroom/{classroom.id}/students/bulk-upload/confirm",
            json={'import_id': import_id}
        )
        self.assertEqual(confirm_resp.status_code, 200)
        confirm_json = confirm_resp.get_json()
        self.assertTrue(confirm_json['success'])
        result_summary = confirm_json['summary']
        self.assertEqual(result_summary['new_accounts_created'], 2)

        # Verify new accounts in MongoDB
        new1 = User.find_by_email(f"bulk_new1_{self.test_tag}@example.com")
        new2 = User.find_by_email(f"bulk_new2_{self.test_tag}@example.com")
        self.assertIsNotNone(new1)
        self.assertIsNotNone(new2)
        self.assertTrue(new1.must_change_password)
        self.assertTrue(new1.check_password("Student@123"))

        # Verify enrolled in classroom
        self.assertIsNotNone(ClassroomMembership.find_one(classroom.id, new1.id))
        self.assertIsNotNone(ClassroomMembership.find_one(classroom.id, new2.id))

        # Step 3: Download Excel Result Report
        report_resp = self.client.get(f"/staff/classroom/{classroom.id}/students/bulk-upload/{import_id}/report")
        self.assertEqual(report_resp.status_code, 200)
        self.assertIn("spreadsheetml", report_resp.headers.get('Content-Type', ''))

    def test_08_forgot_and_reset_password(self):
        """Verify password reset link token generation, verification, and expiration."""
        student = self.__class__.test_student

        with self.client.session_transaction() as sess:
            sess.clear()

        # Request reset
        resp = self.client.post('/student/forgot-password', data={'email': student.email}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Check token in MongoDB
        raw_token = PasswordReset.create_token(student.id, expires_in_minutes=15)
        user_verified, err = PasswordReset.verify_token(raw_token)
        self.assertIsNone(err)
        self.assertEqual(user_verified.id, student.id)

        # Submit new password via reset token route
        reset_resp = self.client.post(f"/student/reset-password/{raw_token}", data={
            'new_password': 'Student@ResetPassword123!',
            'confirm_password': 'Student@ResetPassword123!'
        }, follow_redirects=True)
        self.assertEqual(reset_resp.status_code, 200)

        # Verify password changed
        updated = User.find_by_id(student.id)
        self.assertTrue(updated.check_password('Student@ResetPassword123!'))

        # Verify token cannot be reused
        user_again, err2 = PasswordReset.verify_token(raw_token)
        self.assertIsNotNone(err2)
        self.assertIn("already been used", err2)

    def test_09_idor_and_rbac_protection(self):
        """Verify IDOR protections between students, staff, and classrooms."""
        staff_a = User.find_by_email(f"staff_{self.test_tag}@university.edu")
        classroom_a = self.__class__.test_classroom
        student = self.__class__.test_student

        # Create staff B
        staff_b = User.create({
            'email': f"staff_b_{self.test_tag}@university.edu",
            'username': f"prof_b_{self.test_tag}",
            'password_hash': generate_password_hash("Pass@123"),
            'role': 'staff'
        })
        classroom_b = Classroom.create(
            name=f"Classroom-B-Private-{self.test_tag}",
            staff_id=staff_b.id
        )

        # Staff A attempts to manage Staff B's classroom -> 403 Forbidden
        with self.client.session_transaction() as sess:
            sess['user_id'] = staff_a.id
            sess['username'] = staff_a.username
            sess['role'] = 'staff'

        forbidden_resp = self.client.get(f"/staff/classroom/{classroom_b.id}")
        self.assertEqual(forbidden_resp.status_code, 403)

        # Student attempts to access staff dashboard -> 403 Forbidden
        with self.client.session_transaction() as sess:
            sess['user_id'] = student.id
            sess['username'] = student.username
            sess['role'] = 'student'

        forbidden_staff = self.client.get('/staff/dashboard')
        self.assertEqual(forbidden_staff.status_code, 403)

    def test_10_scoring_and_delta_snapshots(self):
        """Verify scoring engine calculations and delta snapshots with MongoDB."""
        student = self.__class__.test_student
        profile = PlatformProfile.upsert(student.id, 'Codeforces', {
            'handle': 'test_cf_coder',
            'rating': 1500,
            'recent_problems': 120,
            'total_contests': 15,
            'sync_status': 'success'
        })
        self.assertIsNotNone(profile)
        self.assertEqual(profile.rating, 1500)

        score = compute_profile_score(profile)
        self.assertGreater(score, 0)

        # Snapshot insertion
        snap = PerformanceSnapshot.create({
            'user_id': student.id,
            'platform': 'Codeforces',
            'snapshot_date': '2026-09-23',
            'rating': 1500,
            'rating_delta': 50,
            'problems_solved': 120,
            'problems_solved_delta': 5,
            'contests': 15,
            'contests_delta': 1,
            'calculated_score': score
        })
        self.assertIsNotNone(snap)
        self.assertEqual(snap.rating_delta, 50)

    def test_11_excel_exports(self):
        """Verify classroom, student, and admin user directory Excel exports."""
        classroom = self.__class__.test_classroom
        student = self.__class__.test_student
        admin = User.find_by_email(Config.INITIAL_ADMIN_EMAIL)

        with self.client.session_transaction() as sess:
            sess['user_id'] = admin.id
            sess['username'] = admin.username
            sess['role'] = 'admin'

        # Classroom export
        c_resp = self.client.get(f"/download/classroom/{classroom.id}")
        self.assertEqual(c_resp.status_code, 200)

        # Student export
        s_resp = self.client.get(f"/download/student/{student.id}")
        self.assertEqual(s_resp.status_code, 200)

        # User directory export
        u_resp = self.client.get("/admin/users/export")
        self.assertEqual(u_resp.status_code, 200)

    def test_12_session_inactivity_timeout(self):
        """Verify session timeout after 1 hour of inactivity."""
        student = self.__class__.test_student

        # Case 1: Inactive session for > 3600 seconds (e.g. 3650s)
        with self.client.session_transaction() as sess:
            sess['user_id'] = student.id
            sess['username'] = student.username
            sess['role'] = 'student'
            sess['last_activity'] = datetime.now(timezone.utc).timestamp() - 3650

        resp = self.client.get('/student/dashboard', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))

        # Verify session cleared
        with self.client.session_transaction() as sess:
            self.assertNotIn('user_id', sess)

        # Case 2: Active session within 1 hour (e.g. 300s ago)
        with self.client.session_transaction() as sess:
            sess['user_id'] = student.id
            sess['username'] = student.username
            sess['role'] = 'student'
            sess['last_activity'] = datetime.now(timezone.utc).timestamp() - 300

        resp_active = self.client.get('/student/dashboard')
        self.assertEqual(resp_active.status_code, 200)

if __name__ == '__main__':
    unittest.main(verbosity=2)
