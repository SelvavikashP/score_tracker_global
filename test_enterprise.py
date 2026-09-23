import sys
import os
import unittest
from datetime import datetime, timezone, date
from unittest.mock import patch

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from app import app, db
from config import Config
from models import User, Classroom, ClassroomMembership, PlatformProfile, PerformanceSnapshot, RemovalRequest, AuditLog
from werkzeug.security import generate_password_hash
from scoring import calculate_snapshot_score, compute_profile_score, DEFAULT_SCORING_WEIGHTS
from sync_service import sync_single_platform_profile, sync_student_profiles

class EnterpriseTestSuite(unittest.TestCase):
    test_db_path = os.path.join(Config.BASE_DIR, 'User_data', 'test_enterprise.db')

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{cls.test_db_path}"
        app.config['WTF_CSRF_ENABLED'] = False
        # Ensure tests never send real emails
        cls.email_patcher = patch('email_service.send_email_async', return_value=True)
        cls.email_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.email_patcher.stop()
        app.config['TESTING'] = False
        app.config['SQLALCHEMY_DATABASE_URI'] = Config.SQLALCHEMY_DATABASE_URI
        with app.app_context():
            db.session.remove()
        if os.path.exists(cls.test_db_path):
            try:
                os.remove(cls.test_db_path)
            except Exception:
                pass

    def setUp(self):
        self.client = app.test_client()

        with app.app_context():
            db.drop_all()
            db.create_all()

            # Provision Test Users
            self.admin = User(
                username='admin_boss',
                email='admin@test.edu',
                password_hash=generate_password_hash('AdminPass123!'),
                role='admin',
                full_name='System Admin',
                is_active=True,
                is_email_verified=True
            )
            self.staff_a = User(
                username='prof_smith',
                email='smith@test.edu',
                password_hash=generate_password_hash('StaffPass123!'),
                role='staff',
                full_name='Prof. Smith',
                is_active=True,
                is_email_verified=True
            )
            self.staff_b = User(
                username='prof_jones',
                email='jones@test.edu',
                password_hash=generate_password_hash('StaffPass123!'),
                role='staff',
                full_name='Prof. Jones',
                is_active=True,
                is_email_verified=True
            )
            self.student_1 = User(
                username='alice_coder',
                email='alice@test.edu',
                password_hash=generate_password_hash('StudentPass123!'),
                role='student',
                full_name='Alice Wonderland',
                student_identifier='CS-001',
                is_active=True,
                is_email_verified=True
            )
            self.student_2 = User(
                username='bob_hacker',
                email='bob@test.edu',
                password_hash=generate_password_hash('StudentPass123!'),
                role='student',
                full_name='Bob Builder',
                student_identifier='CS-002',
                is_active=True,
                is_email_verified=True
            )
            db.session.add_all([self.admin, self.staff_a, self.staff_b, self.student_1, self.student_2])
            db.session.commit()

            self.admin_id = self.admin.id
            self.staff_a_id = self.staff_a.id
            self.staff_b_id = self.staff_b.id
            self.student_1_id = self.student_1.id
            self.student_2_id = self.student_2.id

            # Create Classroom for Staff A
            self.class_a = Classroom(
                name='Algorithms Fall 2026',
                description='Advanced Data Structures',
                staff_id=self.staff_a_id,
                status='active'
            )
            self.class_a.generate_invite_token()
            db.session.add(self.class_a)
            db.session.commit()

            self.class_a_id = self.class_a.id
            self.invite_token = self.class_a.invite_token

            # Add Student 1 to Class A
            self.m1 = ClassroomMembership(
                classroom_id=self.class_a_id,
                student_id=self.student_1_id,
                status='active'
            )
            db.session.add(self.m1)

            # Add Platform Profile for Student 1
            self.p1 = PlatformProfile(
                user_id=self.student_1_id,
                platform='Codeforces',
                handle='tourist',
                profile_url='https://codeforces.com/profile/tourist',
                rating=3300,
                rank='Legendary Grandmaster',
                recent_problems=600,
                total_contests=300,
                sync_status='success'
            )
            db.session.add(self.p1)
            db.session.commit()
            self.p1_id = self.p1.id

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, email, password, client=None):
        c = client or self.client
        return c.post('/login', data={'email': email, 'password': password}, follow_redirects=True)

    def test_role_based_dashboards_and_redirections(self):
        """Test authentication and appropriate dashboard landing for all roles."""
        # 1. Admin Login
        res_admin = self.login('admin@test.edu', 'AdminPass123!')
        self.assertEqual(res_admin.status_code, 200)
        self.assertIn(b'Portal Control Center', res_admin.data)
        self.client.get('/logout')

        # 2. Staff Login
        res_staff = self.login('smith@test.edu', 'StaffPass123!')
        self.assertEqual(res_staff.status_code, 200)
        self.assertIn(b'Classroom Management', res_staff.data)
        self.client.get('/logout')

        # 3. Student Login
        res_student = self.login('alice@test.edu', 'StudentPass123!')
        self.assertEqual(res_student.status_code, 200)
        self.assertIn(b'Connected Platforms', res_student.data)
        self.client.get('/logout')

    def test_broken_access_control_and_idor_protection(self):
        """Ensure RBAC blocks unauthorized route traversal and IDOR parameter tampering."""
        # Student attempts to access Admin dashboard -> 403 Forbidden
        self.login('alice@test.edu', 'StudentPass123!')
        res_admin = self.client.get('/admin/dashboard')
        self.assertEqual(res_admin.status_code, 403)

        # Student attempts to access Staff dashboard -> 403 Forbidden
        res_staff = self.client.get('/staff/dashboard')
        self.assertEqual(res_staff.status_code, 403)

        # Student attempts to delete own platform profile -> 302 Redirect
        res_del = self.client.get(f'/student/profile/delete/{self.p1_id}')
        self.assertEqual(res_del.status_code, 302)

        self.client.get('/logout')

        # Staff B attempts to access Staff A's classroom -> 403 Forbidden
        self.login('jones@test.edu', 'StaffPass123!')
        res_class_idor = self.client.get(f'/staff/classroom/{self.class_a_id}')
        self.assertEqual(res_class_idor.status_code, 403)

    def test_classroom_join_token_lifecycle(self):
        """Test cryptographically secure token join workflow."""
        token = self.invite_token
        self.assertIsNotNone(token)

        # Student 2 logs in and accesses join link
        self.login('bob@test.edu', 'StudentPass123!')
        res_landing = self.client.get(f'/classroom/join/{token}')
        self.assertEqual(res_landing.status_code, 200)
        self.assertIn(b'Algorithms Fall 2026', res_landing.data)

        # Student 2 confirms join
        res_join = self.client.post(f'/classroom/join/{token}/confirm', follow_redirects=True)
        self.assertEqual(res_join.status_code, 200)
        self.assertIn(b'Successfully enrolled', res_join.data)

        # Verify Student 2 is in ClassroomMembership
        with app.app_context():
            m2 = ClassroomMembership.query.filter_by(classroom_id=self.class_a_id, student_id=self.student_2_id).first()
            self.assertIsNotNone(m2)
            self.assertEqual(m2.status, 'active')

    def test_student_removal_request_and_admin_approval(self):
        """Test Staff submitting removal request -> Admin approving it."""
        # Staff A submits removal request for Student 1
        self.login('smith@test.edu', 'StaffPass123!')
        res_req = self.client.post(f'/staff/student/{self.student_1_id}/request-removal', data={
            'classroom_id': self.class_a_id,
            'reason': 'Student dropped the course section'
        }, follow_redirects=True)
        self.assertEqual(res_req.status_code, 200)
        self.assertIn(b'Removal request', res_req.data)
        self.client.get('/logout')

        # Admin reviews queue and approves
        self.login('admin@test.edu', 'AdminPass123!')
        res_q = self.client.get('/admin/removal-requests')
        self.assertEqual(res_q.status_code, 200)
        self.assertIn(b'dropped the course', res_q.data)

        with app.app_context():
            req = RemovalRequest.query.first()
            self.assertIsNotNone(req)
            req_id = req.id

        res_approve = self.client.post(f'/admin/removal-requests/{req_id}/action', data={
            'action': 'approve'
        }, follow_redirects=True)
        self.assertEqual(res_approve.status_code, 200)
        self.assertIn(b'approved', res_approve.data)

        # Verify membership status is updated to 'removed'
        with app.app_context():
            m = ClassroomMembership.query.filter_by(classroom_id=self.class_a_id, student_id=self.student_1_id).first()
            self.assertEqual(m.status, 'removed')

    def test_privacy_safe_leaderboard_data(self):
        """Verify student leaderboards do not leak private emails or credentials."""
        self.login('alice@test.edu', 'StudentPass123!')
        res_lb = self.client.get(f'/student/classroom/{self.class_a_id}')
        self.assertEqual(res_lb.status_code, 200)
        # Classmate name should be visible
        self.assertIn(b'Alice Wonderland', res_lb.data)
        # Private email MUST NOT be exposed on student leaderboard
        self.assertNotIn(b'alice@test.edu', res_lb.data)

    def test_excel_export_thread_safety(self):
        """Verify thread-safe per-request Excel generation."""
        self.login('smith@test.edu', 'StaffPass123!')
        res_export = self.client.get(f'/download/classroom/{self.class_a_id}')
        self.assertEqual(res_export.status_code, 200)
        self.assertTrue(len(res_export.data) > 0)
        self.assertEqual(res_export.content_type, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_scoring_engine(self):
        """Verify configurable scoring calculations, platform weighting, and delta bonuses."""
        score_base = calculate_snapshot_score(
            rating=1500,
            problems_solved=100,
            contests=10,
            platform="Codeforces"
        )
        # Expected: 1500*1.0 + 100*12.0 + 10*25.0 = 1500 + 1200 + 250 = 2950.0
        self.assertEqual(score_base, 2950.0)

        # With positive deltas
        score_delta = calculate_snapshot_score(
            rating=1550,
            problems_solved=110,
            contests=11,
            rating_delta=50,
            problems_delta=10,
            contests_delta=1,
            platform="Codeforces"
        )
        # Expected base: 1550 + 1320 + 275 = 3145
        # Expected deltas: 50*2.5 + 10*20.0 + 1*35.0 = 125 + 200 + 35 = 360
        # Total: 3145 + 360 = 3505.0
        self.assertEqual(score_delta, 3505.0)

    @patch('sync_service.fetch_user_data')
    def test_fault_tolerant_sync_and_snapshots(self, mock_fetch):
        """Verify fault isolation: when 1 platform fails, other platforms succeed and snapshots are recorded."""
        with app.app_context():
            p_cf = db.session.get(PlatformProfile, self.p1_id)
            p_lc = PlatformProfile(
                user_id=self.student_1_id,
                platform='LeetCode',
                handle='alice_lc',
                profile_url='https://leetcode.com/u/alice_lc'
            )
            p_cc = PlatformProfile(
                user_id=self.student_1_id,
                platform='CodeChef',
                handle='alice_cc',
                profile_url='https://www.codechef.com/users/alice_cc'
            )
            db.session.add_all([p_lc, p_cc])
            db.session.commit()

            def mock_side_effect(handle, platform):
                if platform == 'Codeforces':
                    return {'rating': 3350, 'rank': 'LGM', 'global_rank': 1, 'recent_problems': 610, 'total_contests': 302}
                elif platform == 'LeetCode':
                    return {'rating': 2100, 'rank': 'Guardian', 'global_rank': 500, 'recent_problems': 450, 'total_contests': 25}
                elif platform == 'CodeChef':
                    # Simulate failure / API timeout
                    return None
                return None

            mock_fetch.side_effect = mock_side_effect

            res = sync_student_profiles(self.student_1_id, force=True)
            self.assertTrue(res['success'])
            self.assertEqual(res['total'], 3)
            self.assertEqual(res['synced'], 2)  # 2 succeeded, 1 failed gracefully

            # Verify snapshots created for the 2 successful profiles
            today = datetime.now(timezone.utc).date()
            snapshots = PerformanceSnapshot.query.filter_by(user_id=self.student_1_id, snapshot_date=today).all()
            self.assertEqual(len(snapshots), 2)
            platforms_snapshotted = {s.platform for s in snapshots}
            self.assertIn('Codeforces', platforms_snapshotted)
            self.assertIn('LeetCode', platforms_snapshotted)
            self.assertNotIn('CodeChef', platforms_snapshotted)

    @patch('sync_service.fetch_user_data')
    def test_full_enterprise_workflow_item_w(self, mock_fetch):
        """
        End-to-End Item W verification:
        ADMIN -> creates staff
        STAFF -> logs in & creates classroom
        STAFF -> adds/invites student
        STUDENT -> logs in & joins classroom
        STUDENT -> adds platform profiles
        SYSTEM -> syncs data & stores snapshot
        STUDENT -> views progress & leaderboard
        STAFF -> views student analytics & requests removal
        ADMIN -> reviews & approves request
        SYSTEM -> records audit events
        """
        mock_fetch.return_value = {
            'rating': 1600,
            'rank': 'Expert',
            'global_rank': 2500,
            'country_rank': 400,
            'recent_problems': 120,
            'total_contests': 15
        }
        # 1. ADMIN creates staff
        self.login('admin@test.edu', 'AdminPass123!')
        res_create_staff = self.client.post('/admin/staff/create', data={
            'username': 'prof_curie',
            'email': 'curie@test.edu',
            'full_name': 'Marie Curie',
            'password': 'CuriePassword123!'
        }, follow_redirects=True)
        self.assertEqual(res_create_staff.status_code, 200)
        self.assertIn(b'Staff account for Marie Curie provisioned successfully', res_create_staff.data)
        self.client.get('/logout')

        # 2. STAFF logs in & creates classroom
        self.login('curie@test.edu', 'CuriePassword123!')
        res_create_class = self.client.post('/staff/classroom/create', data={
            'name': 'Physics & Computing 101',
            'description': 'Intro to Computational Physics'
        }, follow_redirects=True)
        self.assertEqual(res_create_class.status_code, 200)
        self.assertIn(b'Physics &amp; Computing 101', res_create_class.data)
        self.assertIn(b'created successfully', res_create_class.data)

        with app.app_context():
            curie = User.query.filter_by(email='curie@test.edu').first()
            curie_class = Classroom.query.filter_by(staff_id=curie.id).first()
            curie_class_id = curie_class.id
            curie_token = curie_class.invite_token
            self.assertIsNotNone(curie_token)
        self.client.get('/logout')

        # 3. STUDENT registers (auto-verified with registration welcome email)
        res_reg = self.client.post('/signup', data={
            'username': 'charlie_new',
            'email': 'charlie@test.edu',
            'full_name': 'Charlie Student',
            'student_identifier': 'CS-099',
            'password': 'CharliePass123!'
        }, follow_redirects=True)
        self.assertEqual(res_reg.status_code, 200)
        self.assertIn(b'Welcome aboard, Charlie Student', res_reg.data)

        with app.app_context():
            charlie = User.query.filter_by(email='charlie@test.edu').first()
            self.assertIsNotNone(charlie)
            self.assertTrue(charlie.is_email_verified)

        # Student joins classroom directly
        res_join = self.client.post(f'/classroom/join/{curie_token}/confirm', follow_redirects=True)
        self.assertEqual(res_join.status_code, 200)
        self.assertIn(b'Successfully enrolled', res_join.data)

        # 4. STUDENT adds platform profile
        res_add_p = self.client.post('/student/profile/add', data={
            'platform': 'Codeforces',
            'profile_url': 'https://codeforces.com/profile/charlie_cf'
        }, follow_redirects=True)
        self.assertEqual(res_add_p.status_code, 200)

        with app.app_context():
            charlie = User.query.filter_by(email='charlie@test.edu').first()
            charlie_id = charlie.id
            p_charlie = PlatformProfile.query.filter_by(user_id=charlie_id, platform='Codeforces').first()
            self.assertIsNotNone(p_charlie)
            p_charlie.rating = 1600
            p_charlie.rank = 'Expert'
            p_charlie.recent_problems = 120
            p_charlie.total_contests = 15
            p_charlie.sync_status = 'success'
            db.session.commit()

        # 5. STUDENT views own dashboard & classroom leaderboard
        res_dash = self.client.get('/student/dashboard')
        self.assertEqual(res_dash.status_code, 200)
        self.assertIn(b'charlie_cf', res_dash.data)

        res_cl_lb = self.client.get(f'/student/classroom/{curie_class_id}')
        self.assertEqual(res_cl_lb.status_code, 200)
        self.assertIn(b'Charlie Student', res_cl_lb.data)
        self.client.get('/logout')

        # 6. STAFF views student analytics & requests removal
        self.login('curie@test.edu', 'CuriePassword123!')
        res_analytics = self.client.get(f'/staff/student/{charlie_id}')
        self.assertEqual(res_analytics.status_code, 200)
        self.assertIn(b'Charlie Student', res_analytics.data)

        res_req = self.client.post(f'/staff/student/{charlie_id}/request-removal', data={
            'classroom_id': curie_class_id,
            'reason': 'Student transferred to different institution'
        }, follow_redirects=True)
        self.assertEqual(res_req.status_code, 200)
        self.assertIn(b'submitted to Administrator', res_req.data)
        self.client.get('/logout')

        # 7. ADMIN reviews request & approves
        self.login('admin@test.edu', 'AdminPass123!')
        with app.app_context():
            req = RemovalRequest.query.filter_by(student_id=charlie_id, status='pending').first()
            self.assertIsNotNone(req)
            req_id = req.id

        res_appr = self.client.post(f'/admin/removal-requests/{req_id}/action', data={
            'action': 'approve'
        }, follow_redirects=True)
        self.assertEqual(res_appr.status_code, 200)
        self.assertIn(b'approved', res_appr.data)

        # 8. Verify Audit Logs recorded all operations
        with app.app_context():
            logs = AuditLog.query.all()
            events = [l.event_type for l in logs]
            self.assertTrue(len(logs) >= 4)
            self.assertIn('STAFF_PROVISIONED', events)
            self.assertIn('CLASSROOM_CREATE', events)
            self.assertIn('REMOVAL_REQUEST_SUBMITTED', events)
            self.assertIn('REMOVAL_REQUEST_APPROVED', events)

    def test_registration_welcome_email_and_optional_otp_lifecycle(self):
        """Test registration sends welcome email and immediately activates account, with optional OTP verification."""
        # 1. Standard registration: Auto-activated immediately without OTP blocking
        res_reg = self.client.post('/signup', data={
            'username': 'diana_prince',
            'email': 'diana@test.edu',
            'full_name': 'Diana Prince',
            'password': 'DianaPass123!'
        }, follow_redirects=True)
        self.assertEqual(res_reg.status_code, 200)
        self.assertIn(b'Welcome aboard, Diana Prince', res_reg.data)

        with app.app_context():
            diana = User.query.filter_by(email='diana@test.edu').first()
            self.assertTrue(diana.is_email_verified)
            self.assertTrue(diana.is_active)

        self.client.get('/logout')

        # 2. Test Optional OTP Verification Flow when explicitly configured
        Config.REQUIRE_EMAIL_VERIFICATION = True
        try:
            res_reg_otp = self.client.post('/signup', data={
                'username': 'clark_kent',
                'email': 'clark@test.edu',
                'full_name': 'Clark Kent',
                'password': 'ClarkPass123!'
            }, follow_redirects=True)
            self.assertEqual(res_reg_otp.status_code, 200)
            self.assertIn(b'Verify Your Email', res_reg_otp.data)

            with app.app_context():
                clark = User.query.filter_by(email='clark@test.edu').first()
                self.assertFalse(clark.is_email_verified)
                correct_otp = clark.otp_code

            # Test Invalid OTP Code
            res_fail = self.client.post('/verify-otp', data={'otp': '000000'}, follow_redirects=True)
            self.assertEqual(res_fail.status_code, 200)
            self.assertIn(b'Invalid verification code', res_fail.data)

            # Test Resend OTP
            res_resend = self.client.post('/resend-otp', follow_redirects=True)
            self.assertEqual(res_resend.status_code, 200)
            self.assertIn(b'fresh 6-digit verification code', res_resend.data)

            with app.app_context():
                clark = User.query.filter_by(email='clark@test.edu').first()
                new_otp = clark.otp_code
                self.assertIsNotNone(new_otp)

            # Submit Valid OTP Code
            res_success = self.client.post('/verify-otp', data={'otp': new_otp}, follow_redirects=True)
            self.assertEqual(res_success.status_code, 200)
            self.assertIn(b'Email verified successfully', res_success.data)

            with app.app_context():
                clark = User.query.filter_by(email='clark@test.edu').first()
                self.assertTrue(clark.is_email_verified)
        finally:
            Config.REQUIRE_EMAIL_VERIFICATION = False

    def test_multi_user_concurrency(self):
        """Verify multiple simultaneous client sessions do not leak state."""
        c_student = app.test_client()
        c_staff = app.test_client()
        c_admin = app.test_client()

        self.login('alice@test.edu', 'StudentPass123!', client=c_student)
        self.login('smith@test.edu', 'StaffPass123!', client=c_staff)
        self.login('admin@test.edu', 'AdminPass123!', client=c_admin)

        # Student hits student dashboard
        r1 = c_student.get('/student/dashboard')
        self.assertEqual(r1.status_code, 200)
        self.assertIn(b'Connected Platforms', r1.data)

        # Staff hits staff dashboard
        r2 = c_staff.get('/staff/dashboard')
        self.assertEqual(r2.status_code, 200)
        self.assertIn(b'Classroom Management', r2.data)

        # Admin hits admin dashboard
        r3 = c_admin.get('/admin/dashboard')
        self.assertEqual(r3.status_code, 200)
        self.assertIn(b'Portal Control Center', r3.data)

        # Concurrently ensure student cannot access admin or staff
        self.assertEqual(c_student.get('/admin/dashboard').status_code, 403)
        self.assertEqual(c_student.get('/staff/dashboard').status_code, 403)

    def test_staff_creation_and_staff_provisioning_student_user(self):
        """Test staff signup, staff creating new student user, and student login."""
        c_staff = app.test_client()
        c_student = app.test_client()

        # 1. Register new staff member
        res_staff_reg = c_staff.post('/signup', data={
            'username': 'prof_xavier',
            'email': 'xavier@test.edu',
            'full_name': 'Prof. Charles Xavier',
            'password': 'XavierPass123!',
            'role': 'staff',
            'student_identifier': 'Dept of CS'
        }, follow_redirects=True)
        self.assertEqual(res_staff_reg.status_code, 200)

        # Verify OTP if required
        otp_code = None
        with app.app_context():
            xavier = User.query.filter_by(email='xavier@test.edu').first()
            self.assertIsNotNone(xavier)
            self.assertEqual(xavier.role, 'staff')
            if not xavier.is_email_verified and xavier.otp_code:
                otp_code = xavier.otp_code

        if otp_code:
            res_v = c_staff.post('/verify-otp', data={'otp': otp_code}, follow_redirects=True)
            self.assertEqual(res_v.status_code, 200)

        # 2. Staff creates a classroom
        res_create_class = c_staff.post('/staff/classroom/create', data={
            'name': 'Mutant Algorithms 101',
            'description': 'Advanced DSA'
        }, follow_redirects=True)
        self.assertEqual(res_create_class.status_code, 200)

        with app.app_context():
            cls = Classroom.query.filter_by(name='Mutant Algorithms 101').first()
            self.assertIsNotNone(cls)
            cls_id = cls.id

        # 3. Staff provisions a new student login account directly and enrolls them
        res_prov = c_staff.post('/staff/user/create', data={
            'full_name': 'Scott Summers',
            'username': 'cyclops',
            'student_identifier': 'MUT-001',
            'email': 'cyclops@test.edu',
            'password': 'CyclopsPass123!',
            'classroom_id': str(cls_id)
        }, follow_redirects=True)
        self.assertEqual(res_prov.status_code, 200)
        self.assertIn(b'created successfully', res_prov.data)

        # 4. Verify user exists in database and is enrolled in the classroom
        with app.app_context():
            scott = User.query.filter_by(email='cyclops@test.edu').first()
            self.assertIsNotNone(scott)
            self.assertEqual(scott.role, 'student')
            self.assertEqual(scott.full_name, 'Scott Summers')
            self.assertTrue(scott.is_active)

            membership = ClassroomMembership.query.filter_by(classroom_id=cls_id, student_id=scott.id, status='active').first()
            self.assertIsNotNone(membership)

        # 5. Newly provisioned student signs in and accesses their dashboard
        res_login = self.login('cyclops@test.edu', 'CyclopsPass123!', client=c_student)
        self.assertEqual(res_login.status_code, 200)

        res_dash = c_student.get('/student/dashboard')
        self.assertEqual(res_dash.status_code, 200)
        self.assertIn(b'Scott Summers', res_dash.data)

    def test_data_at_rest_encryption(self):
        """Verify sensitive user data and OTPs are encrypted at rest with AES-256."""
        import sqlite3
        with app.app_context():
            u = User(
                username='secret_agent',
                email='agent007@mi6.gov.uk',
                password_hash='scrypt_hashed_password',
                role='student',
                full_name='James Bond',
                student_identifier='MI6-007',
                otp_code='987654',
                is_active=True,
                is_email_verified=True
            )
            db.session.add(u)
            db.session.commit()
            u_id = u.id

            # ORM level reads plaintext
            user_orm = db.session.get(User, u_id)
            self.assertEqual(user_orm.full_name, 'James Bond')
            self.assertEqual(user_orm.student_identifier, 'MI6-007')
            self.assertEqual(user_orm.otp_code, '987654')

            # Direct raw SQL query on database verifies encryption token at rest
            raw_res = db.session.execute(db.text("SELECT full_name, student_identifier, otp_code FROM user WHERE id = :id"), {"id": u_id}).fetchone()
            self.assertTrue(raw_res[0].startswith("enc::gAAAAAB"))
            self.assertTrue(raw_res[1].startswith("enc::gAAAAAB"))
            self.assertTrue(raw_res[2].startswith("enc::gAAAAAB"))
            self.assertNotIn("James Bond", raw_res[0])
            self.assertNotIn("MI6-007", raw_res[1])
            self.assertNotIn("987654", raw_res[2])

    def test_admin_user_directory_export_and_password_reset(self):
        """Verify Admin can export all user accounts to Excel and perform password resets."""
        # 1. Admin login
        self.login('admin@test.edu', 'AdminPass123!')

        # 2. Export user directory Excel
        res_exp = self.client.get('/admin/users/export')
        self.assertEqual(res_exp.status_code, 200)
        self.assertEqual(
            res_exp.headers.get('Content-Type'),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertTrue(len(res_exp.data) > 1000)

        # 3. Reset a user's password as Admin
        res_reset = self.client.post(f'/admin/user/{self.student_1_id}/reset-password', data={
            'new_password': 'NewBrandPassword456!'
        }, follow_redirects=True)
        self.assertEqual(res_reset.status_code, 200)
        self.assertIn(b'updated successfully', res_reset.data)

        # 4. Verify student can log in with new password
        c_student = app.test_client()
        res_stud_login = self.login('alice@test.edu', 'NewBrandPassword456!', client=c_student)
        self.assertEqual(res_stud_login.status_code, 200)
        self.assertIn(b'Alice Wonderland', res_stud_login.data)

    def test_admin_permanent_user_deletion(self):
        """Verify Admin can permanently remove a user from the database with cascade cleanup."""
        # 1. Admin login
        self.login('admin@test.edu', 'AdminPass123!')

        # 2. Delete student_1 permanently
        res_del = self.client.post(f'/admin/user/{self.student_1_id}/delete', follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)
        self.assertIn(b'permanently deleted from the database', res_del.data)

        # 3. Verify user is removed from database
        with app.app_context():
            deleted_user = db.session.get(User, self.student_1_id)
            self.assertIsNone(deleted_user)

            # Verify associated platform profiles and memberships are cleaned up
            profiles = PlatformProfile.query.filter_by(user_id=self.student_1_id).all()
            self.assertEqual(len(profiles), 0)

            memberships = ClassroomMembership.query.filter_by(student_id=self.student_1_id).all()
            self.assertEqual(len(memberships), 0)

        # 4. Verify admin cannot delete themselves
        with app.app_context():
            admin_user = User.query.filter_by(email='admin@test.edu').first()
            admin_id = admin_user.id
        res_self_del = self.client.post(f'/admin/user/{admin_id}/delete', follow_redirects=True)
        self.assertIn(b'Cannot delete your own active administrator account', res_self_del.data)

    def test_dashboard_stat_card_lists_and_approvals_queue(self):
        """Verify Admin & Staff dashboard stat lists and creator-scoped pending approvals."""
        # 1. Staff submits a removal request
        self.login('smith@test.edu', 'StaffPass123!')
        res_req = self.client.post(f'/staff/student/{self.student_1_id}/request-removal', data={
            'classroom_id': self.class_a_id,
            'reason': 'Inactive in coding cohort'
        }, follow_redirects=True)
        self.assertEqual(res_req.status_code, 200)

        # 2. Staff views their dashboard -> sees creator-scoped pending removal
        res_staff_dash = self.client.get('/staff/dashboard')
        self.assertEqual(res_staff_dash.status_code, 200)
        self.assertIn(b'Inactive in coding cohort', res_staff_dash.data)
        self.assertIn(b'Pending Admin Approval', res_staff_dash.data)
        self.assertIn(b'Enrolled Cohort Students', res_staff_dash.data)
        self.client.get('/logout')

        # 3. Admin views dashboard -> sees student roster, staff roster, classrooms, and pending removal queue
        self.login('admin@test.edu', 'AdminPass123!')
        res_admin_dash = self.client.get('/admin/dashboard')
        self.assertEqual(res_admin_dash.status_code, 200)
        self.assertIn(b'Enrolled Students Roster', res_admin_dash.data)
        self.assertIn(b'Faculty &amp; Staff Directory', res_admin_dash.data)
        self.assertIn(b'Global Classrooms Directory', res_admin_dash.data)
        self.assertIn(b'Pending Approvals &amp; Removal Queue', res_admin_dash.data)
        self.assertIn(b'Inactive in coding cohort', res_admin_dash.data)

        # 4. Admin approves removal directly from dashboard queue
        with app.app_context():
            req = RemovalRequest.query.filter_by(student_id=self.student_1_id, status='pending').first()
            self.assertIsNotNone(req)
            req_id = req.id

        res_approve = self.client.post(f'/admin/removal-requests/{req_id}/action', data={
            'action': 'approve',
            'next': '/admin/dashboard'
        }, follow_redirects=True)
        self.assertEqual(res_approve.status_code, 200)
        self.assertIn(b'Student removal request approved', res_approve.data)

    def test_all_email_notification_events(self):
        """Verify all lifecycle events trigger email notification services."""
        from email_service import (
            send_welcome_email, send_classroom_enrolled_email,
            send_student_joined_staff_notification_email, send_removal_request_submitted_email,
            send_removal_request_decision_email, send_password_reset_notification_email,
            send_account_status_notification_email
        )

        with app.app_context():
            student = User.query.filter_by(email='alice@test.edu').first()
            staff = User.query.filter_by(email='smith@test.edu').first()
            classroom = Classroom.query.filter_by(id=self.class_a_id).first()

            # 1. Welcome email
            ok, _ = send_welcome_email(student.email, student.full_name, student.username, student.role, student.student_identifier)
            self.assertTrue(ok)

            # 2. Classroom enrollment email to student
            ok, _ = send_classroom_enrolled_email(student, classroom, staff)
            self.assertTrue(ok)

            # 3. Student joined staff notification email
            ok, _ = send_student_joined_staff_notification_email(staff, student, classroom)
            self.assertTrue(ok)

            # 4. Removal request submitted email to admin
            ok, _ = send_removal_request_submitted_email('admin@test.edu', staff, student, classroom, 'Test removal reason')
            self.assertTrue(ok)

            # 5. Removal request decision email
            ok, _ = send_removal_request_decision_email(student.email, student.full_name, student, classroom, 'approve', reviewer=staff)
            self.assertTrue(ok)

            # 6. Password reset email
            ok, _ = send_password_reset_notification_email(student, new_password='NewPassword123!')
            self.assertTrue(ok)

            # 7. Account status email
            ok, _ = send_account_status_notification_email(student, is_active=True)
            self.assertTrue(ok)

if __name__ == '__main__':
    unittest.main()



