# Deploying to Render: Step-by-Step

Follow these instructions to deploy your **Global Platform Score Tracker** to Render as a live web service.

## 1. Prepare your Repository
- Ensure all changes (including the updated `render.yaml`) are pushed to your GitHub repository.

## 2. Connect to Render
1.  Go to [Render.com](https://render.com) and log in.
2.  Click the **"New +"** button and select **"Web Service"**.
3.  Connect your GitHub/GitLab account if you haven't already.
4.  Select the repository for this project.

## 3. Configure the Service
Render will automatically detect your `render.yaml` if you use the "Blueprints" feature, but for a standard Web Service setup, use these settings:

-   **Name**: `score-tracker-global`
-   **Environment**: `Python 3`
-   **Build Command**: `pip install -r requirements.txt`
-   **Start Command**: `gunicorn app:app`
-   **Plan**: `Free`

## 4. Environment Variables
1.  In the Render dashboard for your project, go to the **"Environment"** tab.
2.  Add the following variables:
    *   **SECRET_KEY**: `your_long_random_secret_string`
    *   **PYTHON_VERSION**: `3.10.12` (Optional, but recommended)
3.  Click **"Save Changes"**.

## 5. Persistence Note
> [!IMPORTANT]
> Since this project uses **SQLite**, data will be reset whenever the service restarts or redeploys (on the Free plan). 
> 
> To keep your data permanently, you would typically need a **Render Blueprint with a Disk** (paid) or a managed database like **Supabase (Postgres)**.

## 6. Access your App
Once the build is complete (usually 2-3 minutes), Render will provide a URL like `https://score-tracker-global.onrender.com`. Open it to see your live leaderboard!
