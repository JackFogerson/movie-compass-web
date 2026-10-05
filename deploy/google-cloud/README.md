# Google Cloud always-running free-tier deployment

This is the primary family-and-friends beta target. Google Cloud's ongoing Free Tier currently
includes enough monthly Compute Engine time for one non-preemptible `e2-micro` VM, 30 GB-months
of standard persistent disk, and 1 GB of North American outbound transfer in `us-west1`,
`us-central1`, or `us-east1`. The VM does not have an idle sleep policy. A billing account is
required, and exceeding a free monthly allowance can create charges, so enable budgets and alerts.

The `e2-micro` has only 1 GB RAM. This deployment therefore runs one Uvicorn worker directly under
systemd and uses durable SQLite in WAL mode rather than a PostgreSQL container. That is suitable
for a small, low-concurrency family beta and keeps all account/profile data on persistent disk.
PostgreSQL remains the upgrade path for a larger public audience.

## Owner console steps

1. Create a Google Cloud billing account and project. Upgrade the trial billing account to a paid
   billing account while keeping all resources within Free Tier limits; otherwise trial resources
   stop when the trial ends.
2. Create one Ubuntu 24.04 `e2-micro` VM in `us-west1`, `us-central1`, or `us-east1`. Use a 30 GB
   **standard persistent disk**, not balanced or SSD disk. Reserve its external IPv4 address.
3. Create billing budgets/alerts. A budget alerts but does not cap spending, so also avoid any
   resource not listed in the Free Tier allowance.
4. Allow inbound TCP 80 and 443. Restrict SSH to the owner's IP when practical.
5. Point the website domain's `A` record to the reserved IP.

## VM installation

Install Python 3.12, Git, and Caddy, then clone the web repository to `/opt/movie-compass`. Create a
dedicated `moviecompass` system user, a virtual environment, and install the project:

```bash
sudo useradd --system --home /opt/movie-compass --shell /usr/sbin/nologin moviecompass
sudo git clone https://github.com/JackFogerson/movie-compass-web.git /opt/movie-compass
sudo chown -R moviecompass:moviecompass /opt/movie-compass
sudo -u moviecompass python3 -m venv /opt/movie-compass/.venv
sudo -u moviecompass /opt/movie-compass/.venv/bin/pip install --no-cache-dir /opt/movie-compass
sudo install -d -o moviecompass -g moviecompass /var/lib/movie-compass /var/backups/movie-compass
```

Create a two-GiB swap file to absorb short memory spikes during imports/model loading:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Copy `movie-compass.env.example` to `/etc/movie-compass.env`, make it readable only by root and the
service group, and fill in the real secrets on the VM. Never put them in Git or chat. Copy the
systemd service and enable it:

```bash
sudo cp /opt/movie-compass/deploy/google-cloud/movie-compass.service /etc/systemd/system/
sudo chown root:moviecompass /etc/movie-compass.env
sudo chmod 640 /etc/movie-compass.env
sudo systemctl daemon-reload
sudo systemctl enable --now movie-compass
```

Replace the example domain in `Caddyfile.example`, install it as `/etc/caddy/Caddyfile`, and restart
Caddy. Caddy obtains and renews HTTPS certificates automatically after DNS and ports are correct.

## Backups

Create a consistent online backup (do not copy a live WAL database directly):

```bash
sudo -u moviecompass /opt/movie-compass/.venv/bin/python \
  /opt/movie-compass/deploy/google-cloud/backup-sqlite.py \
  /var/lib/movie-compass/movie-compass.sqlite3 /var/backups/movie-compass
```

Schedule that daily with a systemd timer or cron. Keeping a second encrypted copy outside the VM
is required before treating real profiles as durable. Test a restore before public release.

## Verification

- `/health` returns a process response.
- `/ready` confirms the database, bundled catalog, and TMDB configuration.
- Registration, login, email reset, profile import/export, search, recommendations, and shared
  movie night all pass on the public HTTPS address.
- Reboot the VM and confirm systemd restarts both the application and Caddy automatically.
