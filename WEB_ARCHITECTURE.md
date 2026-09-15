# Movie Compass Web Architecture

## Product boundary

The website is a multi-account service. The downloadable local edition remains private to one computer. A profile moves between them only through an explicit Movie Compass profile export and import.

## Current account model

- `accounts`: login identity, display name, and Argon2 password hash.
- `users`: existing taste-profile records, now owned by an account through `owner_account_id`.
- One account can own any number of taste profiles.
- Profile IDs remain globally unique in the initial web release. The UI reports a collision instead of accessing another account's profile.
- Signed HTTP-only cookies authenticate browser requests. Production cookies must be HTTPS-only.

## Social model reserved for the next phase

- `friendships`: directional request with `pending`, `accepted`, or `blocked` status.
- `profile_shares`: an explicit grant from a profile owner to another account.
- The first sharing permission is `movie_night`: the recipient may use a shared profile in group scoring but cannot see its rating history, reviews, model diagnostics, or export.
- Movie-night APIs will resolve the union of owned profiles and accepted shares. Every mutation continues to require ownership.

## Persistence required before public launch

PostgreSQL is authoritative for accounts, profiles, ratings, reviews, mappings, and imports. Generated personal recommendations and model-weight files currently use the filesystem; the next deployment step is to move those small JSON documents into PostgreSQL or S3-compatible object storage. Shared MovieLens/TMDB artifacts remain immutable build assets.

## Security launch checklist

1. Set a unique `WEB_SESSION_SECRET` and rotate the TMDB key previously used for local development.
2. Require HTTPS and `WEB_COOKIE_SECURE=true`.
3. Add email verification, password reset, login throttling, and upload-rate limits.
4. Add CSRF tokens before enabling cross-account mutations or friendship actions.
5. Add account deletion, profile deletion, data export, privacy policy, and retention rules.
6. Run imports/ranking in a background worker with visible job status instead of holding one HTTP request open.
7. Store no Letterboxd ZIP after import; retain only rating-bearing entries and the user's requested review text.

## Suggested free preview deployment

- Render free web service for the Docker container.
- Supabase or Neon free PostgreSQL for persistent account/profile data.
- A later object-store integration for generated ranking JSON.

Free application containers have ephemeral disks. Do not call the website production-ready until every profile-specific file has moved off local disk.
