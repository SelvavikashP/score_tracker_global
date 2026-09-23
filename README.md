# Global Platform Score Tracker — Multi-Role Performance Portal

A unified, multi-role academic portal to track competitive programming and coding platform performance across **Codeforces**, **LeetCode**, **CodeChef**, **AtCoder**, and **HackerRank**.

Designed for universities, colleges, bootcamps, and coding clubs to track student growth, conduct classroom leaderboards, and monitor historical performance trends.

---

## 🏛️ Architecture & Database Highlights

```
┌─────────────────────────────────────────────────────────────┐
│                       PORTAL USERS                          │
│        [Admin]             [Staff / Faculty]      [Student] │
└──────────┬─────────────────────────┬───────────────────┬────┘
           │                         │                   │
           ▼                         ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│                     FLASK WEB APPLICATION                   │
│   • Server-Side RBAC & IDOR Auth Decorators                 │
│   • Staff-Controlled Student Provisioning (Email-Based)     │
│   • Two-Step Bulk Import (.xlsx, .csv, .txt)                │
│   • First-Login Password Change Enforcement Guard           │
│   • Dark / Light Mode Responsive UI (320px–1920px)          │
│   • Thread-Safe Isolated Excel Exporter                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
┌───────────────────────┐       ┌─────────────────────────────┐
│ MONGODB ATLAS (Native)│       │    SYNCHRONIZATION ENGINE   │
│ • Users & RBAC        │◄──────┤  • Fault-Tolerant Clients   │
│ • Classrooms & Members│       │  • Daily Snapshots Service  │
│ • Platform Profiles   │       │  • Configurable Scoring     │
│ • Daily Snapshots     │       │  • Background APScheduler   │
│ • Bulk Imports & Audit│       └──────────────┬──────────────┘
│ • Password Resets     │                      │
└───────────────────────┘                      ▼
                                ┌─────────────────────────────┐
                                │    EXTERNAL PLATFORM APIS   │
                                │ • Codeforces  • LeetCode    │
                                │ • CodeChef    • AtCoder     │
                                │ • HackerRank                │
                                └─────────────────────────────┘
```

### Key Architectural Pillars

1. **MongoDB Atlas Native Storage**:
   - Sole application database runtime using PyMongo.
   - Robust compound unique indexes:
     - `users.email_normalized` (unique)
     - `classroom_memberships(classroom_id, student_id)` (unique)
     - `platform_profiles(user_id, platform)` (unique)
     - `performance_snapshots(user_id, platform, snapshot_date)` (unique)
     - `password_resets.token_hash` (unique) + TTL index on `expires_at`
2. **Staff-Controlled Student Provisioning**:
   - Public student self-registration is disabled.
   - Faculty/Admin enroll students directly via email.
   - Newly created students receive initial default password (`Student@123`), stored strictly as a secure hash (`generate_password_hash`).
   - Mandatory first-login password change guard (`must_change_password=True`) redirects student to `/student/change-password` before any dashboard or classroom access.
   - Existing student passwords are never overwritten upon classroom enrollment.
3. **Two-Step Bulk Import (`.xlsx`, `.csv`, `.txt`)**:
   - Upload → Validate & Parse → Staging Preview → Server-Side Tamper-Proof Confirm → Batch Provision & Enroll → Download Result Report (`.xlsx`).
   - Handles case variations (`email`, `Email`, `student_email`, `mail`), duplicates in file, existing accounts, and wrong-role accounts.
4. **Decoupled Fast Page Rendering**:
   - Web requests never block on slow third-party platform API calls. Dashboards render immediately from stored database state.
   - Fault-isolated platform synchronizations.
5. **Configurable Scoring Engine (`scoring.py`)**:
   - Standardized scoring formula normalizing platform rating scales with configurable metric weights and delta improvement bonuses.
6. **Strict RBAC & Object-Level Authorization**:
   - Full IDOR protection preventing unauthorized access across classrooms, student accounts, and administrative actions.

---

## 👥 Role Workflows

| Role | Responsibilities & Capabilities |
| :--- | :--- |
| **Admin** | Portal-wide governance, provisioning faculty accounts, toggling user statuses, reviewing/approving student removal requests, inspecting audit trail, system-wide synchronizations. |
| **Staff** | Creating classrooms, single student enrollment by email, bulk importing student cohorts via `.xlsx`/`.csv`/`.txt`, viewing deep-dive student performance analytics, submitting removal requests, exporting `.xlsx` reports. |
| **Student** | Logging in with email, changing temporary password on first login, connecting coding platform handles, viewing personal progress graphs, exploring privacy-safe classroom and universal leaderboards. |

---

## 🚀 Getting Started Locally

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.12)
- MongoDB Atlas Cluster or local MongoDB instance
- Git

### 2. Installation

Clone the repository and install dependencies:
```bash
git clone https://github.com/SelvavikashP/score_tracker_global.git
cd score_tracker_global
pip install -r requirements.txt
```

### 3. Environment Configuration

Copy the example environment template:
```bash
cp .env.example .env
```

Edit `.env` to configure your environment:
```ini
FLASK_ENV=development
FLASK_DEBUG=True
SECRET_KEY=your_secure_random_key

# MongoDB Atlas Configuration
MONGODB_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/
MONGODB_DB=score_tracker

# Base Application URL (controls all email and external URLs)
APP_BASE_URL=http://127.0.0.1:5000

# Default Credentials
DEFAULT_STUDENT_PASSWORD=Student@123
ENABLE_DEMO_DATA=false

# Initial Admin Bootstrap (only bootstrapped once if no admin exists)
INITIAL_ADMIN_EMAIL=admin@scoretracker.io
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=ConfigureYourSecurePasswordHere!
INITIAL_ADMIN_FULLNAME=System Administrator

# Optional: Live SMTP Email Notifications
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM_EMAIL=no-reply@scoretracker.io
```

### 4. Database Migration (Optional)

To migrate historical data from an existing SQLite or PostgreSQL database to MongoDB Atlas:
```bash
python migrate_to_mongo.py --sqlite database.db
```

### 5. Run the Application

```bash
python app.py
```

Open your browser and navigate to `http://127.0.0.1:5000`.

---

## 🧪 Running Automated Tests

Execute the comprehensive end-to-end acceptance test suite covering MongoDB operations, authentication, staff provisioning, bulk import, IDOR protection, delta scoring, and exports:

```bash
python test_enterprise_mongo.py
```

---

## ☁️ Production Deployment on Render

1. Connect your repository to [Render](https://render.com).
2. Render automatically detects `render.yaml`.
3. Configure the following environment variables in the Render Dashboard under **Environment**:
   - `MONGODB_URI`: Your secure MongoDB Atlas connection string.
   - `MONGODB_DB`: `score_tracker`
   - `APP_BASE_URL`: `https://score-tracker-z8a8.onrender.com`
   - `DEFAULT_STUDENT_PASSWORD`: `Student@123`
   - `INITIAL_ADMIN_EMAIL`: `admin@scoretracker.io`
   - `INITIAL_ADMIN_PASSWORD`: Your strong administrator password.
   - `SESSION_COOKIE_SECURE`: `true`
4. Deploy the service. The application automatically initialises indexes and bootstraps the administrator on first boot.

---

## 🔒 Security Hardening

- **Zero Hardcoded Secrets**: Secrets are read strictly from environment variables.
- **Audit Trails**: All key lifecycle events (logins, logouts, provisions, enrollments, bulk imports, password changes, removals) are logged to MongoDB `audit_logs`.
- **Hashed Password Security**: Passwords and password reset tokens are stored exclusively as cryptographic hashes.
- **Anti-Tamper Staged Imports**: Bulk import confirmation is validated server-side by authenticated staff ID and classroom ownership.
