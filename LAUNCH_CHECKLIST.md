# Movie Compass website launch checklist

## What the owner needs to provide

1. **Google Cloud account/project:** create a billing account, then create one Free Tier-eligible
   `e2-micro` Ubuntu VM in `us-west1`, `us-central1`, or `us-east1` with a 30 GB standard persistent
   disk. Reserve its IP and enable billing budgets/alerts. The billing account remains necessary
   even when usage stays inside the ongoing monthly Free Tier allowance.
2. **Public domain:** point the website domain's `A` record to the VM's reserved IP. Caddy will
   obtain HTTPS automatically after DNS is live.
3. **Resend DNS records:** at the domain's DNS provider, copy every record exactly as displayed by
   Resend. Use the host/name without duplicating the root domain, leave TTL at Auto/default, and
   make email CNAMEs DNS-only if the DNS provider offers HTTP proxying. Then click **Verify DNS
   Records** in Resend.
4. **Server-only configuration:** put `TMDB_API_KEY`, `RESEND_API_KEY`, `EMAIL_FROM`, and a newly
   generated `WEB_SESSION_SECRET` only in `/etc/movie-compass.env` on the VM. Never commit them.
5. **Firewall:** allow HTTP/HTTPS and restrict SSH to the owner's IP when practical.
6. **Product contact:** choose the support/privacy contact email shown to users.

## What Codex can finish after those values exist

1. Install the lightweight systemd/Caddy deployment using `deploy/google-cloud/README.md`.
2. Run migrations and verify SQLite/WAL persistence across service and VM restarts.
3. Verify liveness/readiness, TMDB connectivity, HTTPS, cookies, and security headers.
4. Test registration and a real six-digit password-reset email end to end.
5. Import/export/restore a disposable profile and verify rankings, search, and shared movie night.
6. Schedule consistent daily database backups, copy them off the VM, and test restoration.
7. Verify the Windows app download and complete the family-beta release audit.

## Free-tier boundaries

- The Google Free Tier currently covers one non-preemptible `e2-micro` VM for the full month in
  eligible US regions, 30 GB-months standard persistent disk, and 1 GB outbound transfer from
  North America. It has no published end date, but limits can change with notice.
- The VM does not sleep for inactivity. Planned maintenance or faults can still cause downtime;
  systemd restarts the application after a reboot.
- Free Tier is a usage allowance attached to a billing account, not an absolute spending cap.
  Budgets alert but do not cap spending, so do not create resources outside the listed allowance.
- The one-GB VM is intended for a low-concurrency family/friends beta. Move to PostgreSQL and a
  larger host before opening the service to substantial public traffic.
