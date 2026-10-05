# Movie Compass website launch checklist

## What the owner needs to provide

1. **Google Cloud account/project:** create a billing account, then create one Free Tier-eligible
   `e2-micro` Ubuntu VM in `us-west1`, `us-central1`, or `us-east1` with a 30 GB standard persistent
   disk and external IPv6. Do not leave external IPv4 attached. Enable billing budgets/alerts. The billing account remains necessary
   even when usage stays inside the ongoing monthly Free Tier allowance.
2. **Public domain and tunnel:** put the domain on Cloudflare's free DNS plan, create a Cloudflare
   Tunnel, and route the chosen public hostname to `http://127.0.0.1:10000`. Cloudflare supplies
   public HTTPS without a paid Google external IPv4 address.
3. **Resend DNS records:** at the domain's DNS provider, copy every record exactly as displayed by
   Resend. Use the host/name without duplicating the root domain, leave TTL at Auto/default, and
   make email CNAMEs DNS-only if the DNS provider offers HTTP proxying. Then click **Verify DNS
   Records** in Resend.
4. **Server-only configuration:** put `TMDB_API_KEY`, `RESEND_API_KEY`, `EMAIL_FROM`, the Cloudflare
   tunnel token, and a newly generated `WEB_SESSION_SECRET` only in `/etc/movie-compass.env` on the
   VM. Never commit them.
5. **Administration:** keep inbound HTTP/HTTPS closed and use Google IAP for administrator SSH.
6. **Product contact:** choose the support/privacy contact email shown to users.

## What Codex can finish after those values exist

1. Install the lightweight systemd/Cloudflare Tunnel deployment using
   `deploy/google-cloud/README.md`.
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
- Google charges for an attached external IPv4 after the first account-wide hour each month. This
  deployment uses free external IPv6 plus Cloudflare Tunnel and removes setup IPv4 immediately.
- Free Tier is a usage allowance attached to a billing account, not an absolute spending cap.
  Budgets alert but do not cap spending, so do not create resources outside the listed allowance.
- The one-GB VM is intended for a low-concurrency family/friends beta. Move to PostgreSQL and a
  larger host before opening the service to substantial public traffic.
