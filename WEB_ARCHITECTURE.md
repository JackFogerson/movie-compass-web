# Movie Compass Web Architecture

## Product boundary

The website is a multi-account service. The downloadable local edition remains private to one computer. A profile moves between them only through an explicit Movie Compass profile export and import.

## Current account model

- `accounts`: login identity, display name, and Argon2 password hash.
- `users`: existing taste-profile records, now owned by an account through `owner_account_id`.
- One account can own any number of taste profiles.
- Profile IDs remain globally unique in the initial web release. The UI reports a collision instead of accessing another account's profile.
- Signed HTTP-only cookies authenticate browser requests. Production cookies must be HTTPS-only.

## Social model

- `friendships`: directional request with `pending`, `accepted`, or `blocked` status.
- `profile_shares`: an explicit grant from a profile owner to another account.
- The Friends screen now supports email-based requests, acceptance, cancellation, and removal.
- Friendship alone exposes no profile, rating, review, export, or model data.
- The first sharing permission is `movie_night`: an owner can grant or revoke individual profiles, and the recipient may use them in group scoring but cannot see rating history, reviews, model diagnostics, or exports.
- Movie-night APIs resolve the union of owned profiles and explicit `movie_night` grants. Every profile mutation continues to require ownership.

## Persistence

The configured persistent database is authoritative for accounts, profiles, ratings, reviews,
mappings, imports, generated recommendation reports, and per-profile review-policy decisions.
The family beta uses one-process SQLite in WAL mode on a Google persistent disk to fit within the
one-GB free VM. PostgreSQL remains the multi-instance/public-scale target. Personal scoring layers
are fitted on demand from database ratings instead of being required as durable model files.
Shared MovieLens/TMDB artifacts remain immutable build assets.

## Security launch checklist

1. Set a unique `WEB_SESSION_SECRET` and rotate the TMDB key previously used for local development.
2. Require HTTPS and `WEB_COOKIE_SECURE=true`.
3. Registration and password reset use emailed, rate-limited six-digit codes with short expiries;
   a successful password reset revokes older sessions. Move preview throttling to a shared store
   before scaling the web service beyond one instance.
4. CSRF tokens protect cookie-authenticated mutations; keep them covered by integration tests.
5. Account and profile deletion plus profile export are implemented; add the published privacy
   policy and formal retention schedule before a public launch.
6. Profile imports now run after the upload response with database-backed private job status.
   Move execution from the web process to a dedicated worker before scaling beyond one instance.
7. Store no Letterboxd ZIP after import; retain only rating-bearing entries and the user's requested review text.

Production startup fails closed unless the database is PostgreSQL or the owner explicitly enables
an absolute persistent SQLite path for the single-VM beta. It also validates authentication,
secure cookies, the session secret, TMDB access, password-reset email, session lifetime, and rate
limits. Browser responses also
include a restrictive content-security policy and related security headers.
`/health` is a lightweight process-liveness check. Hosting and container orchestration use
`/ready`, which also verifies the database, bundled recommendation catalog, and TMDB setup.

## Primary family deployment

- Render Free runs one Docker/Uvicorn worker and supplies the public HTTPS hostname. Its expected
  cold start after 15 idle minutes is accepted in exchange for requiring no payment card.
- Neon Free provides persistent PostgreSQL independently from Render's disposable filesystem.
- Google OpenID Connect signs users in without stored passwords, verification email, Resend, a
  purchased domain, or DNS configuration.
- The repository retains Google Cloud and Oracle deployment files as alternatives, but they are
  not the recommended no-card route. See `deploy/render-neon/README.md`.
