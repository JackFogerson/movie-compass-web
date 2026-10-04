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

PostgreSQL is authoritative for accounts, profiles, ratings, reviews, mappings, imports,
generated recommendation reports, and per-profile review-policy decisions. The application
keeps optional local JSON mirrors for development, but every user-visible saved ranking can be
restored from PostgreSQL after a container restart. Personal scoring layers are fitted on demand
from database ratings instead of being required as durable model files. Shared MovieLens/TMDB
artifacts remain immutable build assets.

## Security launch checklist

1. Set a unique `WEB_SESSION_SECRET` and rotate the TMDB key previously used for local development.
2. Require HTTPS and `WEB_COOKIE_SECURE=true`.
3. Add email verification and password reset; move preview throttling to a shared store before
   scaling the web service beyond one instance.
4. CSRF tokens protect cookie-authenticated mutations; keep them covered by integration tests.
5. Account and profile deletion plus profile export are implemented; add the published privacy
   policy and formal retention schedule before a public launch.
6. Run imports/ranking in a background worker with visible job status instead of holding one HTTP request open.
7. Store no Letterboxd ZIP after import; retain only rating-bearing entries and the user's requested review text.

## Suggested free preview deployment

- Render free web service for the Docker container.
- Supabase or Neon free PostgreSQL for persistent account/profile data.
- Optional object storage later if profile exports or larger generated artifacts need retention.

Free application containers have ephemeral disks. Profile-specific ranking and review-policy
state now survives in PostgreSQL; local cache files may be discarded and rebuilt safely.
