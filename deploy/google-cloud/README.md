# Google Cloud always-running free-tier deployment

This is the primary family-and-friends beta target. Google Cloud's ongoing Free Tier currently
includes enough monthly Compute Engine time for one non-preemptible `e2-micro` VM, 30 GB-months
of standard persistent disk, and 1 GB of North American outbound transfer in `us-west1`,
`us-central1`, or `us-east1`. The VM has no idle sleep policy. A billing account is required and
usage beyond a monthly allowance can create charges.

An always-attached external IPv4 address is **not** free. This design instead gives the VM a free
external IPv6 address and publishes the site through a Cloudflare Tunnel. The tunnel is outbound
only, requires no public IPv4 or inbound web ports, and terminates public HTTPS at Cloudflare.

The `e2-micro` has only 1 GB RAM. The deployment runs one Uvicorn worker under systemd and uses
durable SQLite in WAL mode instead of a PostgreSQL container. This is suitable for a small,
low-concurrency family beta. PostgreSQL remains the upgrade path for a larger public audience.

## Owner console steps

1. Create a Google Cloud billing account and project. Upgrade the trial billing account to a paid
   billing account while keeping resources inside Free Tier limits; otherwise trial resources stop
   when the trial ends.
2. Create a custom-mode VPC with a dual-stack or IPv6-only subnet whose IPv6 access type is
   **External**. Place it in `us-west1`, `us-central1`, or `us-east1`.
3. Create one Ubuntu 24.04 `e2-micro` VM on that subnet. Use a 30 GB **standard persistent disk**,
   select a non-Spot/non-preemptible provisioning model, enable external IPv6, and do not keep an
   external IPv4 attached after setup.
4. Create billing budgets/alerts. Ordinary Compute Engine budgets alert but do not stop usage.
5. Put the domain on Cloudflare's free DNS plan. Create a Cloudflare Tunnel and a public hostname
   such as `movies.example.com` whose service is `http://127.0.0.1:10000`.
6. Keep inbound HTTP/HTTPS closed. Use Google IAP for administrator SSH access.

GitHub does not currently publish an IPv6 address. During the initial installation, an ephemeral
external IPv4 may be attached to the VM and removed immediately after the repository and packages
are installed. Google currently includes only one external-IPv4 hour per account per month; do not
leave it attached. The running application, TMDB, Resend, PyPI, and Cloudflare Tunnel use IPv6.

## VM installation

Install Python 3.12, Git, and `cloudflared`, then clone the web repository to
`/opt/movie-compass`. Create a dedicated service user and install the project:

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

Copy `movie-compass.env.example` to `/etc/movie-compass.env`. Fill in the secrets directly on the
VM, including the tunnel token copied from Cloudflare. Never put them in Git or chat. Install both
systemd units:

```bash
sudo cp /opt/movie-compass/deploy/google-cloud/movie-compass.service /etc/systemd/system/
sudo cp /opt/movie-compass/deploy/google-cloud/cloudflared.service /etc/systemd/system/
sudo chown root:moviecompass /etc/movie-compass.env
sudo chmod 640 /etc/movie-compass.env
sudo systemctl daemon-reload
sudo systemctl enable --now movie-compass cloudflared
```

Confirm the public hostname works, then detach the temporary external IPv4 in Google Cloud. Do not
delete the VM or its standard persistent disk.

## Backups

Create a consistent online backup instead of copying a live WAL database directly:

```bash
sudo -u moviecompass /opt/movie-compass/.venv/bin/python \
  /opt/movie-compass/deploy/google-cloud/backup-sqlite.py \
  /var/lib/movie-compass/movie-compass.sqlite3 /var/backups/movie-compass
```

Schedule that daily. Keep a second encrypted copy outside the VM before treating real profiles as
durable, and test a restore before release.

## Verification

- `/health` returns a process response through the Cloudflare hostname.
- `/ready` confirms the database, bundled catalog, and TMDB configuration.
- Registration, login, email reset, profile import/export, search, recommendations, and shared
  movie night pass on public HTTPS.
- Reboot the VM and confirm systemd restarts the app and tunnel automatically.
