# Deploying to Render — Enterprise PostgreSQL & Web Service

Follow these instructions to deploy the **Global Platform Score Tracker** enterprise portal to [Render.com](https://render.com).

---

## 🚀 Option A: Blueprint Deployment (Recommended — 1-Click)

The repository includes a pre-configured [`render.yaml`](render.yaml) Blueprint that automatically provisions both the **Web Service** and a **Managed PostgreSQL Database**.

1. Log in to [Render.com](https://render.com).
2. Click **"New +"** in the top navigation and select **"Blueprint"**.
3. Connect your GitHub/GitLab repository (`score_tracker_global`).
4. Render will detect `render.yaml` and configure:
   - **Web Service**: `score-tracker-global` (Python 3.12, Gunicorn 2-worker server).
   - **PostgreSQL Database**: `score-tracker-db` (Automatically links `DATABASE_URL`).
   - **Environment Variables**: Auto-generates `SECRET_KEY` and sets up admin credentials.
5. Click **"Apply"**. Render will provision the database, run dependencies installation, apply database migrations automatically on startup, and launch the service.

---

## 🛠️ Option B: Manual Web Service & PostgreSQL Setup

If you prefer to configure components individually:

### 1. Create the PostgreSQL Database
1. In Render, click **"New +"** &rarr; **"PostgreSQL"**.
2. Name: `score-tracker-db`.
3. Plan: **Free** (or Starter for production).
4. Click **"Create Database"**.
5. Copy the **"Internal Database URL"** (for services inside Render) or **"External Database URL"**.

### 2. Create the Web Service
1. In Render, click **"New +"** &rarr; **"Web Service"**.
2. Connect your repository.
3. Configure the service settings:
   - **Name**: `score-tracker-global`
   - **Region**: Same as your PostgreSQL database (e.g. `Oregon`)
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn --workers=2 --bind=0.0.0.0:$PORT app:app`
   - **Plan**: `Free`

### 3. Configure Environment Variables
In the Web Service dashboard &rarr; **"Environment"** tab, add:

| Key | Value / Purpose |
| :--- | :--- |
| `SECRET_KEY` | *(Click "Generate" or provide a secure 64-char random hex)* |
| `DATABASE_URL` | *(Paste the Internal Database URL from step 1)* |
| `INITIAL_ADMIN_EMAIL` | `admin@yourinstitution.edu` |
| `INITIAL_ADMIN_USERNAME` | `portal_admin` |
| `INITIAL_ADMIN_PASSWORD` | *(Set a strong initial password for setup)* |
| `SYNC_CACHE_MINUTES` | `30` |
| `SESSION_COOKIE_SECURE` | `True` |

4. Click **"Save Changes"**. Render will trigger a build and launch the application.

---

## 🗄️ Database Migrations & Persistence

- When `DATABASE_URL` is configured, the application automatically runs all schema checks and creates relational tables via [`migration.py`](migration.py) on initial boot.
- If no administrator exists in the database, the system safely provisions the initial administrator using `INITIAL_ADMIN_EMAIL` and the hashed `INITIAL_ADMIN_PASSWORD`.
- All historical performance snapshots (`PerformanceSnapshot`), classroom memberships, and audit logs remain permanently preserved in PostgreSQL.

---

## 🌐 Post-Deployment Verification

1. Once the deployment finishes, open your Render web service URL (e.g. `https://score-tracker-global.onrender.com`).
2. Log in with your configured `INITIAL_ADMIN_EMAIL` and `INITIAL_ADMIN_PASSWORD`.
3. Navigate to **Admin Portal** &rarr; **Users** to provision faculty accounts.
4. Verify the **Dark/Light Mode toggle** and responsive display on mobile and desktop devices.
