# Pre-deployment checklist

JobScout is built to be **perfect for a single local user / small trusted group today**, with the
structure to become a hosted multi-user product later. This is the one doc to read before exposing it
to **untrusted users**. It has two parts:

- **(A) Flip these flags** — guard rails that are already coded and tested but left **OFF** so they
  don't add friction for a trusted group. Enabling each is a one-line settings change.
- **(B) Build/decide these** — larger items whose *seam* exists but whose implementation is deferred.

Nothing here changes single-user behavior until you act on it. See also
[multi-tenancy.md](multi-tenancy.md) for the global-vs-private data model and the auth/DB drop-in points.

---

## (A) Flip these `settings` flags before deployment

All default to off/lenient (`backend/jobscout/config.py`). Per-account overrides ride on top via
`entitlements.resolve_limits` (`users.plan` / `users.limits_json`) — see part B.

| Flag | Default | What it does | Recommended for hosting |
|---|---|---|---|
| `require_auth` | `False` | 401s every non-exempt route without a valid session (the gate; the provider is B) | `True` — with a real auth provider wired |
| `cors_allow_origins` | `["*"]` | Allowed browser origins (credentials auto-enable only with a concrete list) | your frontend origin(s), e.g. `["https://app.example.com"]` |
| `rate_limit_enabled` | `False` | Per-client request throttle (`rate_limit_per_min`) | `True` |
| `upload_limits_enabled` | `False` | Enforce `max_upload_mb` + `upload_allowed_types` on resume uploads | `True` |
| `max_request_mb` | `None` | Global request-body cap (413 over it) | e.g. `25` |
| `security_headers_enabled` | `False` | X-Frame-Options / X-Content-Type-Options / Referrer-Policy / Permissions-Policy | `True` (safe to enable anytime) |
| `hsts_enabled` | `False` | Adds HSTS (only meaningful over HTTPS) | `True` once on HTTPS |
| `usage_metering_enabled` | `False` | **Record** per-account usage (LLM/tailor/deep-match/requests) for the admin dashboard, WITHOUT capping | `True` (to monitor) |
| `quota_enforced` | `False` | **Cap** per-account usage at the resolved limits (implies recording) | `True` only if you want hard caps |
| `single_user_mode` | `True` | When `False`, admin-only routes (settings/maintenance/scheduler/source-overrides) 403 | `False` |
| `spend_cap` (`llm_spend_per_day`) | `None` | Daily LLM-spend cap per account (`None` = unlimited) | a real number |

**Enabling per-account limits (incl. "no limits for some accounts"):** set that user's `users.plan`
(`'unlimited'` = uncapped) or a `users.limits_json` override (e.g. `{"tailor_per_day": null}`), then flip
`quota_enforced`. No code change — `resolve_limits` already routes every limit through the account. Adding
a brand-new limit is just a new key in `limits_json` + a `<name>_per_day` default; the `usage_counters`
table is generic.

**Operator dashboard & admin access:** the host monitors accounts and grants/revokes premium via the
`/api/admin/*` API (and the frontend **Admin** tab), guarded by `require_admin` — open to the local
operator while `single_user_mode`, and to any `users.is_admin` account once hosting. Flip
`usage_metering_enabled` to start populating per-user usage/storage/traffic. Provision an admin: set a
user's `is_admin=TRUE` (`PATCH /api/admin/users/{id}`) before turning `single_user_mode` off. Real-time
latency/error dashboards (Sentry/Prometheus/Grafana) remain a Tier-B add-on.

---

## (B) Build / decide before public launch

Each already has a **named seam** (Tier 1 left the boundary), so it's a drop-in, not a rewrite.

| Item | Effort | Why safe to defer for a trusted group | The seam |
|---|---|---|---|
| **Real auth provider** (Google OAuth / email+password) | M | Trusted users; `require_auth` off | replace `api/deps.current_user_id` body + add `users` rows; flip `require_auth` |
| **Postgres** (vs embedded DuckDB) | M | One process serves a small group fine; DuckDB is single-writer | implement `RelationalStore` Protocol for Postgres + `relational_backend=postgres` |
| **Object storage** (S3/GCS) for resume/tailored files | S–M | Local disk works on one machine | implement `BlobStore` for S3 + `blob_backend=s3` |
| **Managed/scaled Weaviate** | S | One node holds a small index | point `WeaviateStore` at the managed cluster |
| **Durable job queue** (Redis + RQ/arq) + distributed scheduler lock | M | In-process background jobs are fine at low volume; stale runs are reaped at startup | centralize background dispatch; `runs` tracks state |
| **Per-account quota metering + billing** (Stripe) | M | Trusted group isn't billed | `users.plan` + `usage_counters` + `resolve_limits` are the source of truth |
| **PII encryption at rest** | S–M | Data sits on the operator's own machine | wrap in `BlobStore` + the DB layer |
| **Secret manager** (vs `.env`) | S | Local `.env` is gitignored and off-box only if you deploy | swap the `Settings` load |
| **Data retention / PII auto-purge** | S | Manual `DELETE /api/users/me/data` exists | scheduler + `BlobStore`/DB delete |
| **Audit logging** of sensitive actions | S | Low blast radius with few trusted users | logging config + one decorator |
| **TLS/HTTPS + secure cookies** | S | Local/LAN use | reverse proxy / deploy config; then enable `hsts_enabled` |
| **Observability** (Sentry / metrics / tracing) | S–M | Few users, logs suffice | logging config is in place; add exporters |
| **Backups + restore drills** | S | Single machine, low churn | dump the relational store + Weaviate export (`scripts/`) |
| **Compliance**: privacy policy, ToS, DPA/consent, **sub-processor disclosure** (DeepSeek, NVIDIA, Gemini receive resume text), scraping ToS/legal review, data-residency (DeepSeek is China-hosted) | L | Not distributing to the public | policy docs + the data-lifecycle endpoints |

**Data-residency note:** resume text is sent to DeepSeek/NVIDIA (parse + deep-match) and Google Gemini
(embeddings). For enterprise/EU users, review sub-processors and consider swapping the LLM provider — the
`enrich.chat_json` seam makes the provider a config change.
