# Oracle Cloud Always Free deployment

This deployment keeps Movie Compass running continuously on one Oracle Cloud Ampere A1 VM. It
uses Docker Compose, PostgreSQL with pgvector, and Caddy-managed HTTPS. Services restart after a
VM reboot and the website is not intentionally put to sleep when idle.

## Oracle resources

1. Create an Oracle Cloud account and choose the home region carefully.
2. Create an Always Free Ampere A1 VM using Ubuntu 24.04, within the Always Free CPU and memory
   allowance shown by the Oracle console.
3. Reserve a public IP for the VM.
4. In the subnet security list or network security group, allow inbound TCP 22, 80, and 443 and
   UDP 443. Restrict SSH/22 to the owner's IP whenever practical.
5. Point the chosen domain's `A` record to the reserved public IP.

## Server setup

Install Docker Engine and the Docker Compose plugin using Docker's official Ubuntu instructions,
then clone the website repository on the VM.

```sh
git clone https://github.com/JackFogerson/movie-compass-web.git
cd movie-compass-web/deploy/oracle
cp .env.example .env
chmod 600 .env
```

Edit `.env` on the VM. Never commit it. Supply the domain, two newly generated passwords/secrets,
the rotated TMDB credential, and the Resend sender credentials. Then start the stack:

```sh
docker compose up -d --build
docker compose ps
docker compose logs -f web
```

Caddy obtains and renews the TLS certificate after DNS points to the VM and ports 80/443 are
reachable. The application runs `alembic upgrade head` before every start.

## Backups

Run the included script once to verify database export:

```sh
chmod +x backup-postgres.sh
./backup-postgres.sh
```

For daily local backups, add this cron entry with `crontab -e`:

```cron
17 4 * * * /absolute/path/movie-compass-web/deploy/oracle/backup-postgres.sh >> /var/log/movie-compass-backup.log 2>&1
```

Local backups protect against application mistakes but not loss of the VM. Before treating real
profiles as durable, copy backups to an Oracle Object Storage bucket or another off-VM location
and test restoring one with `pg_restore`.

## Updates

```sh
git pull --ff-only
cd deploy/oracle
docker compose up -d --build
```

Verify `https://YOUR_DOMAIN/ready`, sign-in, TMDB lookup, email reset, profile import, and the
Windows-app download after every production update.
