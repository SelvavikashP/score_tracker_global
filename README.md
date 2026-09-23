# Global Platform Score Tracker — Multi-Role Performance Portal

A unified, multi-role academic portal to track competitive programming and coding platform performance across **Codeforces**, **LeetCode**, **CodeChef**, **AtCoder**, and **HackerRank**.

Designed for universities, bootcamps, and coding clubs to track student growth, conduct classroom leaderboards, and monitor historical performance trends.

---

## 🏛️ Architecture & System Highlights

```
┌─────────────────────────────────────────────────────────────┐
│                       PORTAL USERS                          │
│        [Admin]             [Staff / Faculty]      [Student] │
└──────────┬─────────────────────────┬───────────────────┬────┘
           │                         │                   │
           ▼                         ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│                     FLASK WEB APPLICATION                   │
│   • RBAC & IDOR Auth Decorators                             │
│   • Fast Template Rendering (<50ms from DB)                 │
│   • Dark / Light Mode Responsive UI (320px–1920px)          │
│   • Thread-Safe Isolated Excel Exporter                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
┌───────────────────────┐       ┌─────────────────────────────┐
│  DATABASE (SQLAlchemy)│       │    SYNCHRONIZATION ENGINE   │
│  • PostgreSQL / SQLite│◄──────┤  • Fault-Tolerant Clients   │
│  • Users & RBAC       │       │  • Daily Snapshots Service  │
│  • Classrooms & Joins │       │  • Configurable Scoring     │
│  • Platform Profiles  │       │  • Background APScheduler   │
│  • Daily Snapshots    │       └──────────────┬──────────────┘
│  • Audit Logs         │                      │
└───────────────────────┘                      ▼
                                ┌─────────────────────────────┐
                                │    EXTERNAL PLATFORM APIS   │
                                │ • Codeforces  • LeetCode    │
                                │ • CodeChef    • AtCoder     │
                                │ • HackerRank                │
                                └─────────────────────────────┘
```

### Key Architectural Pillars

1. **Separation of Current Data & Daily Snapshots**:
   - `PlatformProfile`: Contains the latest known real-time statistics.
   - `PerformanceSnapshot`: Append-only daily records capturing rating, problems solved, and contest progress deltas without destroying historical records.
2. **Decoupled Fast Page Rendering**:
   - Web requests never block on slow third-party platform API calls. Dashboards render immediately from stored database state.
   - Synchronization is performed asynchronously or on demand with fault tolerance (e.g. if one platform times out, other platforms and dashboards continue uninterrupted).
3. **Configurable Scoring Engine (`scoring.py`)**:
   - Standardized scoring formula normalizing platform rating scales with configurable metric weights and delta improvement bonuses.
4. **Strict RBAC & Object-Level Authorization**:
   - Full IDOR protection preventing unauthorized access across classrooms, student accounts, and administrative actions.
   - Student classroom views hide private email addresses and identifiers.
5. **Secure Cryptographic Classroom Invitations**:
   - URL-safe tokens with expiration and instant revocation support.

---

## 👥 Role Workflows

| Role | Responsibilities & Capabilities |
| :--- | :--- |
| **Admin** | Portal-wide governance, provisioning faculty accounts, toggling user statuses, reviewing/approving student removal requests, inspecting audit trail, system-wide synchronizations. |
| **Staff** | Creating classrooms, managing student rosters, generating cryptographic join links, viewing student deep-dive analytics & historical snapshot charts, submitting removal requests, exporting `.xlsx` reports. |
| **Student** | Self-enrolling via classroom join links, connecting coding platform handles, viewing personal progress graphs, exploring privacy-safe classroom and universal leaderboards. |

---

## 🚀 Getting Started Locally

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.12)
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

# Optional: Initial admin bootstrap (used on first run if no admin exists)
INITIAL_ADMIN_EMAIL=admin@university.edu
INITIAL_ADMIN_USERNAME=portal_admin
INITIAL_ADMIN_PASSWORD=ConfigureYourSecurePasswordHere!

# Database: SQLite is used by default when DATABASE_URL is not set
# DATABASE_URL=postgresql://user:pass@localhost:5432/scoretracker
```

### 4. Run the Application

```bash
python app.py
```

Open your browser and navigate to `http://127.0.0.1:5000`.

---

## 🧪 Running the Test Suite

Execute the comprehensive automated test suite (RBAC, IDOR protection, Join tokens, Removal workflows, Scoring engine, Fault tolerance, and Multi-user concurrency):

```bash
python test_enterprise.py
```

---

## ☁️ Production Deployment (PostgreSQL & Render)

The project includes pre-configured `render.yaml` infrastructure-as-code:

1. Connect your repository to [Render](https://render.com).
2. Render automatically detects `render.yaml`, provisions a managed PostgreSQL database, and spins up the web service running Gunicorn.
3. Configure environment variables (`SECRET_KEY`, `INITIAL_ADMIN_PASSWORD`) in the Render Dashboard.
4. Database migrations run automatically on startup via `migration.py`.

---

## 🔒 Security Best Practices

- **Zero Hardcoded Credentials**: Administrative credentials, session keys, and database URLs are managed strictly through environment variables.
- **Auditing**: All key lifecycle events (provisioning, deletions, joins, approvals) are recorded in the `AuditLog` table with timestamp and IP address.
- **CSRF & Session Hardening**: HttpOnly, SameSite cookie protection, and hashed password verification via Werkzeug.
