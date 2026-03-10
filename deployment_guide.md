# Deployment Guide: Global Platform Score Tracker

Follow these steps to deploy your Score Tracker to Vercel.

## 1. Prerequisites
- **Node.js installed**: Vercel CLI requires Node.js.
- **Vercel Account**: Sign up at [vercel.com](https://vercel.com).

## 2. Deployment Steps

### Step 1: Install Vercel CLI
Open your terminal (PowerShell or Command Prompt) and run:
```bash
npm i -g vercel
```

### Step 2: Login to Vercel
```bash
vercel login
```
Follow the browser prompts to authenticate.

### Step 3: Deploy the Project
Navigate to your project directory and run:
```bash
vercel --prod
```
- When asked "Set up and deploy?", type `y`.
- For "Which scope?", select your account.
- For "Link to existing project?", type `n`.
- For "What's your project's name?", type `global-platform-score-tracker`.
- For "In which directory is your code located?", press Enter (default `./`).
- For "Want to modify settings?", type `n`.

### Step 4: Configure Environment Variables
1. Go to your [Vercel Dashboard](https://vercel.com/dashboard).
2. Select your project.
3. Navigate to **Settings > Environment Variables**.
4. Add a new variable:
   - **Key**: `SECRET_KEY`
   - **Value**: A random secure string (e.g., `your-random-secret-key-123`).
5. Click **Save**.

## 3. Important Notes on Persistence

> [!WARNING]
> This project uses **SQLite**. In a Vercel serverless environment, the database is stored in the temporary `/tmp` directory.
> 
> **Data will be lost** when the serverless function spins down (after a few minutes of inactivity) or when a new deployment is made. 
> 
> For permanent storage, consider migrating to a hosted database like **Supabase (PostgreSQL)** or **MongoDB Atlas**.
