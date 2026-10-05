# No-card public beta: Render + Neon + Google sign-in

This is the primary public-beta deployment. It needs no purchased domain and no Resend account:

- Render hosts the Docker web service at a free `*.onrender.com` HTTPS address. The free service
  sleeps after 15 minutes without traffic and normally needs about a minute for the first request.
- Neon stores accounts, profiles, ratings, and cached metadata in free PostgreSQL. Do not use
  Render's free PostgreSQL database because that database expires after 30 days.
- Google sign-in verifies the user's email. Movie Compass does not store a password, and no
  verification or password-reset email is required.

## What the owner must do

### 1. Create the Neon database

1. Create a free Neon account and project. No card is required for the free plan.
2. Copy the pooled PostgreSQL connection string. Keep `sslmode=require` in it.
3. Save it temporarily; it becomes Render's `DATABASE_URL` secret.

### 2. Create the Render service

1. Sign in to Render and choose **New > Blueprint**.
2. Connect the `movie-compass-web` GitHub repository and select its `render.yaml`.
3. Give Render these secret values when prompted:
   - `DATABASE_URL`: the pooled Neon connection string.
   - `TMDB_API_KEY`: the TMDB API key used by the service.
4. Render generates `WEB_SESSION_SECRET`. Never publish or reuse that value elsewhere.
5. Note the assigned address, such as `https://movie-compass-web.onrender.com`.

### 3. Create Google sign-in credentials

1. Open Google Cloud Console, create a project, and configure the OAuth consent screen.
2. Choose an external audience. For a small private beta, keep the app in testing and add each
   friend or family member as a test user. Google test mode limits which accounts can sign in.
3. Create an **OAuth client ID** with application type **Web application**.
4. Add this exact authorized redirect URI, replacing the hostname with Render's assigned address:

   `https://movie-compass-web.onrender.com/auth/google/callback`

5. In Render's Environment page set:
   - `GOOGLE_OAUTH_CLIENT_ID` to the generated client ID.
   - `GOOGLE_OAUTH_CLIENT_SECRET` to the generated client secret.
   - `GOOGLE_OAUTH_REDIRECT_URI` to the exact redirect URI from the previous step.
6. Redeploy the latest commit.

Google OAuth configuration does not run paid Google compute. Do not enable unrelated paid Google
Cloud products for this deployment.

## Verification

1. Open the Render address in a private browser window.
2. Choose **Continue with Google** and sign in with a configured test-user account.
3. Import a Letterboxd ZIP, reload the page, and confirm the profile remains.
4. Test a current TMDB title, a recommendation list, and a two-profile movie night.
5. Leave the service idle for at least 20 minutes, then verify that the cold-start page eventually
   opens and the saved profile is still present.

The only expected free-tier inconvenience is the Render cold start. Persistent user data lives in
Neon and is not lost when the Render container sleeps or restarts.
