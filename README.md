# Verve Lifestyle Omnichannel Inventory Platform

Full Stack Engineering Case Study: a small inventory application with a Next.js dashboard, a FastAPI backend, PostgreSQL, and an Odoo integration (mock now, live later).

Detailed rationale is in `docs/design.md`. The implementation plan is in `docs/superpowers/plans/2026-11-13-verve-inventory.md`. Example requests with real output are in `docs/api-examples.md`.

## 1. Architecture and system of record

```
Browser (Next.js dashboard)
        |  JSON over HTTP
        v
FastAPI backend  ---------------------------+
  routers -> services -> repositories       |  Odoo adapter (mock | live)
        |                                   v
        v                          Odoo ERP  (SYSTEM OF RECORD for inventory)
PostgreSQL `database_verve`
(application-owned tables)
        ^
        |
Outbox worker (separate process) --> Odoo adapter
```

Odoo is the authoritative inventory system. The local PostgreSQL stock table is a projection that serves the dashboard and gates sales. It may be briefly ahead of Odoo (sold locally, not yet pushed) and is reconciled by the sync.

| Data | Owner | Notes |
|------|-------|-------|
| Products, variants, locations (master data) | Odoo | Copied locally on sync, never edited locally |
| Authoritative stock quantities | Odoo | |
| `stock_levels` (projection) | Application | Sellable and damaged quantities are separate columns |
| `sales`, `replenishment_requests`, `stock_movements`, `integration_events` | Application | Sales and transfers are pushed to Odoo as documents |
| Schema `mock_odoo` | Mock only | Stands in for Odoo's own database; accessed only by `MockOdooClient` |

The backend never reads or writes Odoo's database directly, and Odoo credentials exist only in backend environment variables.

## 2. Prerequisites

| Tool | Version used |
|------|--------------|
| Python | 3.10 |
| Node.js / npm | 26 / 11 (Node 20 or later should work) |
| PostgreSQL | 14, running locally on port 5432 |

Everything runs natively (no Docker).

## 3. Setup

### 3.1 Database

Create a role and two databases (one for the application, one that the tests empty after every test). Replace `PASSWORD`.

```bash
sudo -u postgres psql \
  -c "CREATE ROLE verve LOGIN PASSWORD 'PASSWORD'" \
  -c "CREATE DATABASE database_verve OWNER verve" \
  -c "CREATE DATABASE database_verve_test OWNER verve"
```

### 3.2 Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then edit .env and set the password
.venv/bin/alembic upgrade head
```

Create the administrator login (it protects the Odoo connection endpoints). The command asks for a password (minimum 12 characters, not echoed) and prints two lines for `backend/.env`; add `ADMIN_USERNAME=admin` (or another name) next to them:

```bash
cd backend && .venv/bin/python -m app.hash_password
```

Environment variables (`backend/.env`):

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Application database (`database_verve`) |
| `TEST_DATABASE_URL` | Test database (`database_verve_test`), emptied by the tests |
| `ODOO_MODE` | `mock` (default) or `live` |
| Odoo URL, database, API key | Not environment variables: enter them on the Odoo configuration page (stored encrypted). Variables of the old names in `.env` are ignored |
| `ODOO_ALLOW_PRIVATE_HOSTS` | `false` by default. Set `true` when Odoo runs on localhost or an internal network (see section 7.4) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `SESSION_SECRET` | Administrator login for the Odoo connection endpoints. Without all three (secret of at least 32 characters) nobody can sign in |
| `SETTINGS_ENCRYPTION_KEY` | Fernet key that encrypts the Odoo API key saved from the Odoo configuration page. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Do not change it after an API key is stored |
| `SESSION_TTL_MINUTES`, `COOKIE_SECURE` | Session length (default 60) and the cookie `Secure` flag; set `COOKIE_SECURE=true` when served over HTTPS |
| `CORS_ORIGINS` | Allowed browser origins, comma separated |

### 3.3 Frontend

```bash
cd frontend
npm install
```

Optional: copy `.env.example` to `.env.local` if the backend is not on `http://localhost:8000`.

## 4. Running


One terminal instead of three: from the project root run `./dev.sh start`, `./dev.sh restart` (after changing `backend/.env`), `./dev.sh stop`, `./dev.sh status` or `./dev.sh logs`. It starts the API (8000), the worker and the dashboard (3000), keeps logs in `.run/`, and only stops processes it started itself. The three manual commands below do the same.
Three terminals:

```bash
# 1. API (seeds the local tables from Odoo on first start when they are empty)
cd backend && .venv/bin/uvicorn app.main:app --port 8000

# 2. Outbox worker (pushes sales and replenishment transfers to Odoo)
cd backend && .venv/bin/python -m app.worker

# 3. Dashboard
cd frontend && npm run dev          # http://localhost:3000
```

Interactive API documentation: `http://localhost:8000/docs`.

To reset the development data (drops all application tables and the mock Odoo state, then recreates them; the next API start re-seeds the sample data):

```bash
cd backend && .venv/bin/alembic downgrade base && .venv/bin/alembic upgrade head
```

## 5. Tests

```bash
cd backend && .venv/bin/pytest -v          # 196 tests, uses real PostgreSQL (database_verve_test)
cd frontend && npm run lint && npm run build
```

| Test file | What it proves |
|-----------|----------------|
| `test_schema.py` | The database itself rejects negative stock, duplicate `request_id`, quantity <= 0 |
| `test_sales_service.py` (unit) | Location per channel, replay vs key reuse, full rollback on insufficient stock, damaged returns never sellable |
| `test_sales_api.py` (integration) | HTTP contract, validation, outbox row written |
| `test_concurrency.py` | Two buyers of the last unit: exactly one succeeds. Two identical concurrent requests: one sale |
| `test_outbox.py` | Retry with backoff, immediate failure on business errors, no double deduction, stale event recovery, two workers never claim one event |
| `test_odoo_mock.py`, `test_odoo_live.py` | Mock behaviour; live client request shape and error mapping against a fake HTTP transport |
| `test_auth.py` | Password hashing, signed session tokens (tamper, expiry, replay after logout, password change), identical login failures, atomic and memory-bounded throttling (per user and per IP), httpOnly cookie, fail-closed configuration |
| `test_url_policy.py` | SSRF policy: metadata, link-local, private and disguised addresses (including 6to4 and NAT64 wrapping), resolved hostnames, schemes, query and fragment, embedded credentials |
| `test_odoo_connection.py` | Connection check without `db.list`, safe error codes, bounded retries (reads only) and total deadline, connection pinned to the validated address, DNS change caught on the next call, no redirects, no environment proxies, response size cap, no secret in status or logs |
| `test_connection_api.py` | Admin-only status API, cached GET, test endpoint ignores request bodies, start-up never fails because of Odoo |
| `test_sync.py`, `test_inventory_api.py`, `test_replenishment_api.py`, `test_events_api.py` | Remaining endpoints and rules |

## 6. API summary

| Method and path | Purpose |
|-----------------|---------|
| `GET /api/inventory?store=&q=&status=` | Variants with stock per location |
| `GET /api/inventory/{variant_id}/movements?limit=` | Recent stock movements |
| `POST /api/inventory/sync` | Refresh the projection from Odoo |
| `POST /api/sales` | Record a sale (idempotent on `request_id`) |
| `GET /api/replenishment` | Store/variant pairs below threshold |
| `POST /api/replenishment/request` | Create a replenishment request |
| `GET /api/integration-events?status=` | Outbox events (troubleshooting; admin only) |
| `POST /api/integration-events/{id}/retry` | Re-queue a FAILED event (admin only) |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | Administrator session (httpOnly cookie); `me` includes the session expiry |
| `PUT /api/auth/password` | Change the administrator password (ends all sessions) |
| `GET`, `PUT`, `DELETE /api/odoo/config` | Odoo URL, database and API key (write-only, encrypted); `DELETE` disconnects (admin only) |
| `GET /api/odoo/connection` | Odoo connection status (admin only; cached, does not call Odoo) |
| `POST /api/odoo/connection/test` | Re-check the connection using the backend configuration (admin only; no credentials in the body) |
| `GET /health` | Liveness and database check |

Errors always have the shape `{"error": {"code", "message", "details"}}`.

## 7. Key behaviours

### 7.1 Sales and concurrency

`POST /api/sales` runs in one PostgreSQL transaction: insert the sale (unique `request_id` is the idempotency gate), decrement stock with a single atomic statement (`UPDATE ... WHERE qty_sellable >= n`), record the movement, write an outbox event, commit. If stock is insufficient the whole transaction rolls back and the key is not consumed.

Two buyers of the last unit: the row lock taken by the first `UPDATE` makes the second wait, then see zero stock and fail with `409 INSUFFICIENT_STOCK`. `CHECK (qty_sellable >= 0)` is a second line of defence. `test_concurrency.py` demonstrates this against real PostgreSQL.

### 7.2 Retries and duplicates

A client that times out and retries with the same `request_id` receives the original sale (`200`, `replayed: true`). The same key with a different payload returns `409 IDEMPOTENCY_KEY_REUSED`.

### 7.3 Odoo integration

| Aspect | Design |
|--------|--------|
| Authentication | API key stored encrypted by the backend, sent as a bearer token to Odoo's JSON-2 API (`/json/2/<model>/<method>`) with the database in `X-Odoo-Database`; no login |
| Contract | `OdooClient` interface, version `v1`; the mock and live clients implement the same interface |
| Delivery | Outbox worker; `FOR UPDATE SKIP LOCKED` so several workers can run |
| Retry | Transient errors (timeout, connection, HTTP 5xx/408/429, unknown Odoo RPC errors) and unexpected errors: exponential backoff (5 s x 2^n), FAILED after 5 attempts. Known business rejections (`UserError`, `ValidationError`, `AccessError`, `MissingError`): FAILED immediately |
| No duplicate deduction | The Odoo reference is always `SALE:<request_id>` or `REPLENISHMENT:<request_id>` (the prefix keeps the two namespaces apart). The live client searches for an existing document with that `origin` before creating one, and finishes an unfinished one (checking that it reached `done`) rather than creating a second |
| Recovery | Signed in as the administrator: `GET /api/integration-events?status=FAILED`, fix the cause, `POST /api/integration-events/{id}/retry` |

Mock demonstration of recovery:

```bash
cd backend && .venv/bin/python -c "
from app.db import engine
from app.odoo.mock import MockOdooClient
MockOdooClient(engine).fail_next('transient', 1)"   # the next Odoo call fails once
```

### 7.4 Odoo connection status and administrator login

One dedicated Odoo service account is configured on the backend: URL, database name and API key (entered on the Odoo configuration page; the connection can be disconnected and pointed at another Odoo project, which asks for a reset of the local data that belongs to the previous Odoo). Odoo's own access rights and record rules stay authoritative for that account. Credentials never reach the browser, and no endpoint accepts them. Per-user Odoo credentials, switching between databases and entering API keys in the frontend are out of scope.

| Aspect | Behaviour |
|--------|-----------|
| Validation | `common.version` then `authenticate` against the configured database. `db.list` is never used because administrators can disable it |
| Status | `GET /api/odoo/connection` returns mode, status, database, Odoo version, last checked time and a sanitised error. It returns the last stored result and does not call Odoo; only start-up and `POST /api/odoo/connection/test` run a check |
| Error codes | `NOT_CONFIGURED`, `URL_NOT_ALLOWED`, `UNREACHABLE`, `TIMEOUT`, `DATABASE_NOT_FOUND`, `AUTH_FAILED`, `UNKNOWN`. Each has a fixed message; raw Odoo text is never returned |
| Start-up | Odoo downtime, a wrong key or missing settings never stop the API. The check and the first data load run in a background thread, so the first inventory request after a cold start can briefly be empty |
| Timeouts and retries | Connect 5 s, read 10 s per attempt, 20 s in total for one connection check; at most 2 retries for authentication and reads, never for writes; responses are capped at 5 MB |
| SSRF | Only http/https, no credentials, query or fragment in the URL; every resolved address is checked, including IPv4 wrapped in IPv6 (6to4, NAT64, Teredo). Link-local (including 169.254.169.254), multicast, unspecified and reserved addresses are always blocked; private and loopback addresses are blocked unless `ODOO_ALLOW_PRIVATE_HOSTS=true`. The check runs on every request and the connection is pinned to the validated address (Host and SNI keep the original name), so DNS rebinding between check and connection is closed. Redirects are not followed and `HTTP(S)_PROXY` variables are ignored |
| Logging and stored errors | Credentials, request bodies and tracebacks from Odoo are never logged or returned; connection failures log only a code. Error text that Odoo sends back is scrubbed of the API key and login (case-insensitive) and truncated to 300 characters; it can appear in server logs and in `last_error`, which only the administrator can read. Unexpected exceptions store only their type name in `last_error` |
| Administrator login | Username plus scrypt password hash and a signed, expiring, httpOnly `SameSite=Lax` cookie. Wrong user and wrong password are indistinguishable. Attempts are reserved atomically before the password is checked and limited to 5 per (IP, username) and 20 per IP in 5 minutes, with bounded memory. Logout revokes the token on the server; changing the admin password invalidates every session; unconfigured login fails closed |

Every endpoint except `/health` and login requires the administrator session (since iteration 3). Odoo credentials can be set from the Odoo configuration page; the API key is write-only and stored encrypted (needs `SETTINGS_ENCRYPTION_KEY`).

## 8. Troubleshooting playbooks

| Symptom | Investigation |
|---------|---------------|
| Stale inventory after a POS sale | Find the sale's event in `GET /api/integration-events` (administrator session required; status, attempts, `next_attempt_at`, `last_error`). Check that the worker is running. Run `POST /api/inventory/sync` and compare with Odoo. Check the browser: responses carry `Cache-Control: no-store` and an `as_of` timestamp, and the dashboard invalidates its queries after every successful sale. |
| Duplicate transactions | Search by `request_id`. The unique constraint makes duplicate rows in `sales` impossible, so look for different keys sent for one intent (client retry logic) or duplicate Odoo documents with the same `origin`. |
| Failed replenishment request | Read `last_error` and `attempts` on its event. Transient: restore Odoo connectivity and retry. Business rejection: fix master data or mapping, then retry the event. |

Logging: standard-library logging with `key=value` lines (`sale.created request_id=...`, `outbox.done event_id=... odoo_ref=...`). Metrics to track in production: outbox backlog size and age of the oldest pending event, count of FAILED events, Odoo call latency and error rate, rate of 409 responses on sales, projection drift against Odoo.

Deployment (documented, not built): containerise backend, worker and frontend separately; run migrations as a release step; keep the Odoo API key in a secret manager; run the worker as its own deployment.

## 9. Assumptions, incomplete items, hardening

Assumptions are listed in `docs/design.md` (marked `[Assumption]`) and in the implementation ledger rulings. The ones that matter most:

- Online sales deduct from the main warehouse (`WH`); store (POS) sales deduct from that store.
- "Below threshold" is strict (`qty < threshold`); the warehouse has no threshold.
- A replenishment request does not move stock locally; stock changes after Odoo completes the transfer and the next sync.

Incomplete or unverified:

| Item | State |
|------|-------|
| Live Odoo client | Verified once against a local Odoo 19.0 Enterprise instance: connection, stock read, one sale posted as a done picking, idempotent replay and re-sync (design 8.3a; three Odoo 19 differences were fixed). Not yet exercised live: replenishment transfers. The detection of a missing database relies on a PostgreSQL-style error message and is unverified. It uses the legacy `/jsonrpc` endpoint, which Odoo 19 deprecates (removal scheduled for Odoo 22); migrating to the JSON-2 API is a hardening item. Other field mapping marked `[ASUMSI]` in `backend/app/odoo/live.py` should be re-checked on other Odoo versions |
| Dashboard behaviour in a browser | Driven in Chromium (login gate, three menus, Odoo configuration, user page, session expiry, phone width). Edge, Firefox and Safari were not tested |
| Authentication and authorisation | The Odoo connection and integration-events endpoints require the administrator login. Every endpoint except `/health` and login requires the single administrator session; there are no per-store roles or multiple users. The Odoo API key can be saved from the UI (write-only, encrypted); a server-side secret store is still the recommended production setup. The login throttle and the session revocation list live in process memory (a restart forgets revoked sessions, and several instances need a shared store); the throttle uses the direct client address and ignores `X-Forwarded-For`, so behind a reverse proxy all clients share one address and an attacker could lock the administrator out. Cross-site request forgery relies on `SameSite=Lax`; an `Origin` check on state-changing requests is a hardening item |
| Damaged return endpoint | Service function and tests exist; no public endpoint |
| Docker, containerisation | Not provided |
| Sync window | If an event completes between reading Odoo stock and computing pending events, the projection can be briefly high until the next sync |
| FAILED sale | Local stock stays deducted: the sync subtracts PENDING, PROCESSING and FAILED sales, so a unit whose delivery to Odoo failed cannot be sold a second time. The operator can retry the event; there is no cancel endpoint to release the stock yet |

Hardening and scaling (two stores to 100): authentication, rate limiting, an Odoo-side unique reference (custom module in `odoo-module/`), a reconciliation job with drift alerts, a dead-letter review screen, incremental sync, read replicas or caching for the dashboard, partitioning of `stock_movements` and `integration_events`, and a message broker if polling becomes a bottleneck. Contention is already limited to a single (variant, location) row, so the sale path scales with the number of stores.
# verve
