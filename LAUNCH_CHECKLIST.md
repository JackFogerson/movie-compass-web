# Movie Compass website launch checklist

## What the owner needs to provide

1. **An Oracle Cloud account:** complete Oracle's identity/card verification and choose the home
   region. Create an Always Free Ampere A1 Ubuntu VM and reserve its public IP. Capacity can vary
   by region, so the owner must perform this console step.
2. **A public domain:** choose a domain or subdomain for the website and point its `A` record to
   the VM's reserved public IP. Caddy will provision HTTPS automatically after DNS is live.
3. **Email delivery:** create a Resend account, verify a sender domain, create a sending API key,
   and add these values only to `deploy/oracle/.env` on the VM:
   - `RESEND_API_KEY`: the sending key;
   - `EMAIL_FROM`: for example `Movie Compass <noreply@movies.example.com>`.
4. **Rotated credentials:** revoke the Resend key pasted into chat and rotate the TMDB credential
   previously displayed in screenshots. Put replacements only in the VM's protected `.env` file.
5. **Oracle firewall rules:** allow HTTP/HTTPS and restrict SSH to the owner's IP when practical.
6. **Product contact:** choose the support/privacy contact email users should see before the site
   is opened beyond family and friends.

Generate `POSTGRES_PASSWORD` and `WEB_SESSION_SECRET` directly on the VM. The Oracle Compose bundle
enables secure HTTPS cookies and production authentication. Secrets never belong in GitHub.

## What Codex can finish after those values exist

1. Install Docker on the Oracle VM, start the included Compose stack, and run every Alembic
   migration through the current head.
2. Verify liveness/readiness, persistent PostgreSQL, TMDB connectivity, and browser security
   headers on the public HTTPS address.
3. Create a test account and verify the six-digit password-reset email end to end.
4. Import, export, and restore a disposable profile; verify rankings and movie-night sharing.
5. Verify the Windows-app download from the deployed website.
6. Configure daily `pg_dump` exports and copy them to Oracle Object Storage or another off-VM
   destination before real profiles are treated as durable.
7. Run a final privacy/security review and publish the initial family beta.

## Current free-tier expectations

- The Oracle VM is not intentionally put to sleep after a short idle period, but Oracle account
  and Always Free eligibility rules still apply.
- Resend Free currently advertises 3,000 emails per month and 100 per day.

Provider limits can change; verify their official pricing pages at deployment time.
