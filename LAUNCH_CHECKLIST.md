# Movie Compass website launch checklist

## What the owner needs to provide

1. **Neon account:** create a free project and copy its pooled PostgreSQL connection string. Keep
   `sslmode=require`. The recommended database does not require a card and does not expire after
   30 days.
2. **Render account:** connect the `movie-compass-web` GitHub repository as a Blueprint. Render
   supplies the public `*.onrender.com` HTTPS hostname; no domain purchase or DNS work is needed.
3. **Google OAuth client:** after Render assigns the hostname, create a Web application OAuth
   client and authorize this exact callback:
   `https://YOUR-RENDER-HOST.onrender.com/auth/google/callback`.
4. **Server-only secrets:** enter the Neon `DATABASE_URL`, `TMDB_API_KEY`, Google client ID, Google
   client secret, and exact Google callback URL in Render. Never commit those values.
5. **Google test users:** while the OAuth consent screen remains in testing, list each friend or
   family member who should be allowed to sign in.
6. **Product contact:** choose the support/privacy contact shown to users before a broader release.

The detailed click-by-click instructions are in `deploy/render-neon/README.md`.

## What Codex can finish after those values exist

1. Confirm the Render Blueprint and Neon connection settings without exposing any secret.
2. Verify Alembic migrations, readiness, TMDB connectivity, HTTPS cookies, and security headers.
3. Test Google sign-in in a private window with one allowed test account.
4. Import/export/restore a disposable profile and verify rankings, search, and shared movie night.
5. Test cold-start behavior after at least 20 idle minutes and confirm profiles remain in Neon.
6. Verify the Windows app download and complete the family-beta release audit.

## Free-tier boundaries

- Render's free web service sleeps after 15 minutes without traffic. The first visit after sleep
  normally takes about a minute; later requests are immediate while the service stays awake.
- The free web service receives 750 instance-hours per month. One low-traffic service normally
  fits because sleeping time does not consume those hours.
- The Render container filesystem is disposable. Personal data must remain in Neon, not local
  files inside the container.
- Do not create a Render Free PostgreSQL database for this project; that product expires after 30
  days. Neon is the durable free database in this plan.
- Google sign-in replaces verification and reset emails. Resend and a custom sending domain are
  not required.

Production startup fails closed unless authentication, secure cookies, a strong session secret,
TMDB, and a persistent database are configured. `/health` is process liveness; `/ready` also checks
the database and bundled recommendation catalog.
