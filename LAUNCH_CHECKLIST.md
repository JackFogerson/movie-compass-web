# Movie Compass website launch checklist

## What the owner needs to provide

1. **A hosting account:** create or sign in to Render and connect the
   `JackFogerson/movie-compass-web` GitHub repository. The included `render.yaml` creates the
   web service. The free service is suitable for a family preview but sleeps after 15 idle
   minutes.
2. **Persistent PostgreSQL:** create a Neon PostgreSQL project and copy its pooled connection
   string into Render as `DATABASE_URL`. Do not use a free Render PostgreSQL database for saved
   profiles because it expires after 30 days.
3. **Email delivery:** create a Resend account, verify a sender domain, create a sending API key,
   and add these Render secrets:
   - `RESEND_API_KEY`: the sending key;
   - `EMAIL_FROM`: for example `Movie Compass <noreply@movies.example.com>`.
4. **TMDB credential:** rotate the TMDB credential that was previously displayed in screenshots,
   then add the replacement to Render as `TMDB_API_KEY`. Do not paste production credentials into
   GitHub or documentation.
5. **Public address:** choose either the free Render address initially or a custom domain. A
   domain used for email must also receive the DNS records supplied by Resend.
6. **Product contact:** choose the support/privacy contact email users should see before the site
   is opened beyond family and friends.

Render generates `WEB_SESSION_SECRET`; the blueprint already enables secure HTTPS cookies and
production authentication. Secrets belong only in the host's secret manager.

## What Codex can finish after those values exist

1. Deploy the Render blueprint and run every Alembic migration through the current head.
2. Verify liveness/readiness, PostgreSQL persistence, TMDB connectivity, and browser security
   headers on the public HTTPS address.
3. Create a test account and verify the six-digit password-reset email end to end.
4. Import, export, and restore a disposable profile; verify rankings and movie-night sharing.
5. Verify the Windows-app download from the deployed website.
6. Configure and test database backups or scheduled `pg_dump` exports before real profiles are
   treated as durable.
7. Run a final privacy/security review and publish the initial family beta.

## Current free-tier expectations

- Render free web services sleep on idle and have ephemeral local files. PostgreSQL remains the
  source of truth.
- Neon Free currently advertises 1 GB of storage per project with no time limit.
- Resend Free currently advertises 3,000 emails per month and 100 per day.

Provider limits can change; verify their official pricing pages at deployment time.
