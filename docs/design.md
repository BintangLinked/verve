# Verve Lifestyle Omnichannel Inventory Platform: Design Document

Status: Draft for approval
Date: 2026-11-13
Scope: Full Stack Engineering Case Study (Tasks 1 to 5)

Items marked **[Decision]** were agreed in discussion. Items marked **[Assumption]** are the author's choices and are open to correction.

---

## 1. Intent

Build a small inventory application for Verve Lifestyle (one central warehouse, two stores, one online storefront) that:

1. Shows stock per location with low-stock status.
2. Records sales safely, with no overselling and no duplicate sales on retry.
3. Highlights and requests store replenishment.
4. Integrates with Odoo, which is the authoritative inventory system, through a backend-only adapter. A mock is used until an Odoo instance is available.

The deliverable is a runnable codebase plus an architecture description that can be defended in the follow-up technical discussion.

## 2. Goals, Non-Goals, Anti-Goals

### 2.1 Goals

| # | Goal |
|---|------|
| G1 | Inventory dashboard with store-wise stock, search, store and status filters, a detail drawer with recent movements, and loading, empty, error and refresh states. |
| G2 | Four REST endpoints as specified (inventory, sales, replenishment list, replenishment request) with validation, clear errors, logging, and controller/service/repository separation. |
| G3 | A concurrency-safe, idempotent sale path demonstrated by an automated test. |
| G4 | An outbox-based Odoo integration with retries, error classification, and protection against duplicate deductions. |
| G5 | At least one unit test and one API integration test, plus written troubleshooting, monitoring and deployment notes. |

### 2.2 Non-Goals

- Multi-user management and roles. A single administrator account exists (see 8.2.2).
- Multi-line carts, pricing, payments, taxes, customers.
- A public endpoint for damaged returns (the data model supports it; see 6.3).
- Docker and docker-compose (documented as production hardening; see 13).
- Redis, Celery or any message broker.
- Real-time push (WebSockets, SSE). The UI uses polling and invalidation.
- A custom Odoo module (`odoo-module/`). Created only if a concrete need arises (see 8.6).
- Product and variant maintenance screens. Master data comes from Odoo.

### 2.3 Anti-Goals (failure even if everything else works)

- A unit is sold twice, or stock goes negative.
- A retried request creates a second sale or a second Odoo deduction.
- Odoo credentials reach the browser or the frontend bundle.
- Damaged stock appears as sellable.
- The application reads or writes Odoo's database directly.

## 3. Decisions and Constraints

| Topic | Decision | Source |
|-------|----------|--------|
| Backend | FastAPI (Python), Pydantic validation | [Decision] |
| Frontend | Next.js, React, TypeScript | [Decision], per case study |
| Database | PostgreSQL | [Decision], per case study |
| Odoo | Mock now. Later a localhost Odoo (WSL, no Docker), authenticated with an API key | [Decision] |
| Sale pattern | Commit locally first, push to Odoo through a transactional outbox | [Decision] |
| Locations | Main warehouse and each store are separate stock locations | [Decision] |
| Deduction rule | Store (POS) sales deduct from that store. Online sales deduct from the main warehouse | [Decision] |
| Runtime | Everything runs natively in WSL2, no Docker | [Decision] |
| PostgreSQL | One server, separate databases (`odoo`, `verve_app`) and roles | [Decision] |
| Repository layout | `frontend/`, `backend/`, `odoo-module/` (the last only if needed) | [Decision] |
| Design location | `docs/design.md` | [Decision] |

Constraints: suggested effort is 2 to 3 hours of practical work plus discussion, so scope is kept deliberately small.

## 4. Architecture

```
Browser (Next.js dashboard)
        |  HTTPS / JSON
        v
FastAPI backend  --------------------------+
  routers -> services -> repositories      |
        |                                  |  Odoo adapter (mock | live)
        v                                  v
PostgreSQL `verve_app`             Odoo ERP (system of record)
(app-owned tables)                 JSON-RPC + API key
        ^
        |
Outbox worker (separate process) --------> Odoo adapter
```

### 4.1 System of Record

| Data | Owner | Notes |
|------|-------|-------|
| Products, variants, locations (master data) | Odoo | Copied into local tables on sync. Never edited locally. |
| Authoritative stock quantities | Odoo | |
| Stock projection (`stock_levels`) | Application | Read model that serves the UI and gates sales. Reconciled with Odoo. |
| Sales, replenishment requests, movements, integration events | Application | Operational records that Odoo does not hold. Sales and transfers are pushed to Odoo as documents. |

The local projection is allowed to be briefly ahead of Odoo (sold locally, not yet pushed). It is never the long-term authority.

## 5. Stock Locations and Rules

| Code | Name | Type |
|------|------|------|
| WH | Main Warehouse | WAREHOUSE |
| SHOP1 | Store 1 | STORE |
| SHOP2 | Store 2 | STORE |

- Each variant has two buckets per location: `qty_sellable` and `qty_damaged`. Only `qty_sellable` is shown as available, sold, or counted for low-stock.
- Low-stock threshold is per variant (as in the sample data) and applies to STORE locations. **[Assumption]** The warehouse has no threshold.
- Status per variant per store: `OUT` (sellable = 0), `LOW` (0 < sellable < threshold), `OK` (sellable >= threshold). "Below threshold" is strict, so a quantity equal to the threshold is `OK`. **[Assumption]**
- With the sample data, the only store/variant combination below threshold is Cotton T-Shirt White / L at Store 2 (4 < 5).

Sample data (seeded through the mock Odoo):

| Variant | WH | SHOP1 | SHOP2 | Threshold |
|---------|----|-------|-------|-----------|
| Cotton T-Shirt Black / L | 40 | 8 | 6 | 5 |
| Cotton T-Shirt Black / M | 25 | 10 | 7 | 5 |
| Cotton T-Shirt White / L | 20 | 6 | 4 | 5 |
| Hydrating Face Serum 50 ml | 50 | 20 | 8 | 6 |
| Eco-Friendly Tote Bag | 40 | 15 | 9 | 5 |

## 6. Data Model (PostgreSQL `verve_app`)

All tables are application-owned. Columns marked `odoo_*` are mapping keys to Odoo records.

### 6.1 Tables

| Table | Key columns | Constraints |
|-------|-------------|-------------|
| `locations` | id, code, name, type, odoo_location_id | `code` unique |
| `products` | id, name, odoo_template_id | |
| `variants` | id, product_id, sku, name, odoo_variant_id, low_stock_threshold | `sku` unique, threshold >= 0 |
| `stock_levels` | variant_id, location_id, qty_sellable, qty_damaged, synced_at | PK (variant_id, location_id). `CHECK (qty_sellable >= 0)`, `CHECK (qty_damaged >= 0)` |
| `stock_movements` | id, variant_id, location_id, bucket, type, qty_delta, ref_type, ref_id, created_at | type in SALE, RETURN_DAMAGED, REPLENISHMENT, SYNC_ADJUSTMENT |
| `sales` | id, request_id, payload_hash, variant_id, location_id, qty, channel, odoo_status, created_at | `request_id` unique, qty > 0, channel in POS, ONLINE |
| `replenishment_requests` | id, request_id, store_location_id, variant_id, qty_requested, status, created_at, updated_at | `request_id` unique, qty > 0, status in PENDING, SUBMITTED, FAILED |
| `integration_events` | id, event_type, aggregate_id, idempotency_key, payload (JSONB), status, attempts, next_attempt_at, last_error, created_at, processed_at | `idempotency_key` unique, status in PENDING, PROCESSING, DONE, FAILED |

### 6.2 Normalisation Notes

Products and variants are separate. Stock is stored once per (variant, location). Movements are append-only and are never used to compute the available quantity (the projection is), so reads stay cheap. **[Assumption]**

### 6.3 Damaged Returns

Damaged units live in `qty_damaged`, never in `qty_sellable`. A service function `record_damaged_return` moves quantity into the damaged bucket and writes a `RETURN_DAMAGED` movement. It is covered by a test and shown in the drawer, but no public endpoint is exposed (the API list does not include one).

## 7. Sale Flow and Concurrency

### 7.1 `POST /api/sales`

Request body: `request_id`, `variant_id`, `quantity`, `channel` (`POS` or `ONLINE`), `store_id` (required for `POS`, ignored for `ONLINE`). **[Assumption]** for the `ONLINE` handling of `store_id`.

Location resolution: `POS` uses the store given. `ONLINE` always uses `WH`.

`payload_hash` covers `variant_id`, `quantity`, `channel` and the resolved location code (so `store_id` on an `ONLINE` sale does not affect the hash). **[Assumption]** A request rejected for insufficient stock does not consume its `request_id`; a later retry with the same key is evaluated afresh and may succeed. Only committed sales are replayed. **[Assumption]**

One database transaction, in this order:

1. `INSERT INTO sales ... ON CONFLICT (request_id) DO NOTHING RETURNING *`. This is the idempotency gate. If no row is returned, load the existing sale and compare `payload_hash`:
   - Same payload: return the original result (HTTP 200, `replayed: true`).
   - Different payload: HTTP 409 `IDEMPOTENCY_KEY_REUSED`.
2. `UPDATE stock_levels SET qty_sellable = qty_sellable - :n WHERE variant_id = :v AND location_id = :l AND qty_sellable >= :n RETURNING qty_sellable`. If zero rows are updated, roll back and return HTTP 409 `INSUFFICIENT_STOCK`. The sale row is rolled back with it, so a rejected request does not consume its key. Rejections are logged only.
3. Insert a `stock_movements` row (SALE, negative delta).
4. Insert an `integration_events` row (SALE, status PENDING, `idempotency_key = 'SALE:' + request_id`).
5. Commit. Respond HTTP 201 with the sale, remaining quantity and `odoo_status: PENDING`.

### 7.2 Two Buyers, One Unit

Two requests with different `request_id` values target the last unit. Both execute step 2. PostgreSQL takes a row lock on the first `UPDATE`, so the second waits. After the first commits, the second re-evaluates `qty_sellable >= 1` against 0 and updates no rows. Exactly one commits. `CHECK (qty_sellable >= 0)` is a second line of defence. No application-level lock or `SELECT ... FOR UPDATE` is needed because the check and the decrement are one atomic statement. A pytest test fires both requests in parallel threads against real PostgreSQL and asserts one 201, one 409, stock 0, one sale and one outbox event.

### 7.3 Same Request Retried

Two identical concurrent requests: the second `INSERT ... ON CONFLICT DO NOTHING` waits for the first transaction, then returns no row and is answered as a replay. A client that times out and retries with the same `request_id` therefore never creates a second sale, whether the original committed or not.

## 8. Odoo Integration

### 8.1 Adapter Contract (internal, version 1)

A single `OdooClient` interface with two implementations selected by `ODOO_MODE=mock|live`:

| Operation | Purpose |
|-----------|---------|
| `list_locations()` | Master data |
| `list_variants()` | Master data |
| `get_stock()` | Quantities per (variant, location, bucket) |
| `post_sale(reference, variant, location, qty)` | Deduct stock in Odoo |
| `post_transfer(reference, variant, from_location, to_location, qty)` | Replenishment transfer |

The contract carries a version string (`v1`) in code and logs, so a changed mapping is an explicit new version rather than a silent edit.

### 8.2 Authentication

The connection (URL, database, API key) is entered on the Odoo configuration page and stored by the backend, the API key encrypted (see 8.2.2). Odoo credentials are not read from environment variables; `ODOO_MODE` only selects the built-in mock when nothing is stored. The live client uses the Odoo JSON-2 API (`/json/2/<model>/<method>`) with the API key as a bearer token and the database in the `X-Odoo-Database` header, so no login is needed. Iteration 4 replaced the earlier `/jsonrpc` (`execute_kw`) transport, which Odoo 19 deprecates (removal scheduled for Odoo 22). Verified against Odoo 19.0+e (see 8.3a). The browser only ever calls the FastAPI backend. Keys are never logged.

### 8.2.1 Connection status and administrator login (iteration 2)

- The connection is one service account (URL, database, API key; the login was dropped in iteration 4) held by the backend only. Per-user Odoo credentials and database switching are out of scope. Iteration 3 (8.2.2) supersedes the earlier exclusion of key management in the frontend.
- The configured database is validated directly (version read, then one authenticated `res.users/context_get` call); `db.list` is not used.
- `GET /api/odoo/connection` (stored status) and `POST /api/odoo/connection/test` (re-check from backend configuration, no credentials in the body) return mode, status, database, Odoo version, last checked time and a sanitised error. Both require the administrator session.
- Errors map to fixed codes and messages (`NOT_CONFIGURED`, `URL_NOT_ALLOWED`, `UNREACHABLE`, `TIMEOUT`, `DATABASE_NOT_FOUND`, `AUTH_FAILED`, `UNKNOWN`); credentials and raw authentication responses are never logged or returned.
- Timeouts 5 s connect and 10 s read per attempt, 20 s total per connection check, at most 2 retries for authentication and reads only, responses capped at 5 MB, no redirects, environment proxies ignored. SSRF policy: http/https only, no embedded credentials, query or fragment, all resolved addresses checked (including IPv4 wrapped in IPv6), link-local and metadata addresses always blocked, private and loopback blocked unless `ODOO_ALLOW_PRIVATE_HOSTS=true`. The check runs on every request and the connection is pinned to the validated address with the original Host and SNI.
- Start-up never fails because of Odoo; the check and initial seed run in a background thread.
- The administrator login (username, scrypt hash, signed httpOnly cookie bound to the password hash, server-side revocation on logout, atomic and bounded login throttle, fail-closed when unconfigured) protects these two endpoints and, after the security review, the integration-events endpoints, because `last_error` carries operational error text. **[Assumption]** The scheme was chosen on the author's recommendation and confirmed by "go ahead".
- The frontend shows a read-only Odoo Connection panel (Mock/Live, database, status, version, last checked, Test connection) behind the login.

### 8.2.2 Login gate, stored Odoo configuration and user page (iteration 3)

Decisions by the owner after iteration 2 (the owner explicitly chose to manage the Odoo credentials from the application):

- **Login first.** Every page requires the administrator login; signed-out visitors see only the login page. The API enforces it too: inventory, sales, replenishment and sync endpoints return `401 UNAUTHENTICATED` without a session. `/health` and `POST /api/auth/login` stay public.
- **Menus.** Dashboard (inventory, sale form, replenishment), Odoo configuration, User. Replenishment stays on the dashboard.
- **Odoo configuration page.** `GET /api/odoo/config` returns source (`database`, `mock` or `none`), URL, database and `api_key_set`. `PUT /api/odoo/config` saves URL, database and API key. The API key is write-only: it is encrypted with Fernet (`SETTINGS_ENCRYPTION_KEY`, backend environment) before it is stored in `app_settings`, is never returned, never logged, and a blank value on later saves keeps the stored key. Without the encryption key, saving is refused with `503 ENCRYPTION_NOT_CONFIGURED`.
- **Single source.** The configuration saved in the application is the only source of Odoo credentials (iteration 4 removed the `ODOO_URL`, `ODOO_DB`, `ODOO_LOGIN` and `ODOO_API_KEY` variables; commenting them out of `.env` changes nothing). The API and the worker rebuild the Odoo client when the stored configuration changes (fingerprint of `app_settings`), so no restart is needed.
- **Unchanged guard rails.** The URL is validated with the SSRF policy on save and on every connection. Allowing localhost or internal addresses stays a server setting (`ODOO_ALLOW_PRIVATE_HOSTS`) that the UI cannot change. No endpoint returns credentials.
- **User page.** Shows username, role and session expiry (`GET /api/auth/me`), and changes the password (`PUT /api/auth/password`, at least 12 characters). The new hash is stored in `app_settings` and overrides `ADMIN_PASSWORD_HASH`. The session signing key derives from the hash, so every session ends after the change.
- **Trade-off, stated plainly.** Entering the API key through the browser means it crosses the browser once, over the connection to the backend. It is protected by the login, HTTPS in production, write-only handling and encryption at rest, but it is a weaker position than keeping it only in a server secret store, which remains the recommended production setup.

### 8.2.3 Odoo configuration: three fields, Disconnect and switching project (iteration 4)

Decisions by the owner: the page asks only for URL, database name and API key; the connection can be disconnected and pointed at another Odoo project; and the system must stay safe when the Odoo credentials are commented out of `.env`.

- **Three fields.** The login is gone. The live client moved from JSON-RPC `execute_kw` (needs login plus key) to the JSON-2 API (key only), which also removes the dependency on the deprecated `/jsonrpc` endpoint. **[Assumption]** The migration was the way to meet "URL, database and API key only"; the author chose it and verified it against Odoo 19.0+e.
- **No environment fallback.** Odoo credentials are read only from the stored (encrypted) configuration. In `ODOO_MODE=live` without a stored configuration the application is not connected (`503 ODOO_NOT_CONFIGURED`, status `NOT_CONFIGURED`); it never uses leftover values.
- **Disconnect.** `DELETE /api/odoo/config` deletes the stored URL, database and API key and sets the connection status to `NOT_CONFIGURED`. The API and the worker stop calling Odoo; sales already recorded stay in the outbox and are sent after a reconnect to the same Odoo.
- **Switching project.** The application remembers a fingerprint (server and database) of the last connected Odoo. A stored key is never reused for a different server or database (`422 API_KEY_REQUIRED`). Local catalogue, stock, sales and events carry Odoo record ids, so connecting a different Odoo while such data exists is refused with `409 ODOO_SWITCH_REQUIRES_RESET` until the administrator sends `reset_local_data: true`; the local data is then deleted in the same transaction as the new configuration and reloaded by a sync. Odoo itself is not changed. **[Assumption]** If no fingerprint exists yet (data loaded before this feature), the first saved connection is accepted without a reset.

### 8.3 Field Mapping (live mode, to be confirmed against the instance)

| Application | Odoo |
|-------------|------|
| `variants.odoo_variant_id` | `product.product` id |
| `products.odoo_template_id` | `product.template` id |
| `locations.odoo_location_id` | `stock.location` id |
| Stock quantity | `stock.quant` (`quantity`) per product and location |
| Damaged bucket | `stock.quant` in a designated damaged/scrap location per site |
| Sale deduction | Outgoing `stock.picking` with `origin = request_id`, validated |
| Replenishment | Internal `stock.picking` from WH to the store location, `origin = request_id` |

### 8.3a Verification against Odoo 19.0+e (local instance)

| Item | Result |
|------|--------|
| Version | `19.0+e`, read from `/web/webclient/version_info` |
| Authentication | JSON-2 with `Authorization: bearer <API key>` and `X-Odoo-Database`; a wrong key answers `401`, a wrong database `404` (HTML), a missing model `404` (JSON) |
| Connection check | `res.users/context_get` returns the key owner's `uid`; no login is needed |
| Locations | `WH`, `SHOP1`, `SHOP2` read from `stock.location` |
| Variants and stock | `product.product`, `stock.quant`, `stock.warehouse.orderpoint` (reorder rules give the low-stock thresholds) |
| Sale | Outgoing `stock.picking` created and validated (`action_confirm`, `action_assign`, `button_validate` with `skip_immediate` and `skip_backorder`); the picking must be `done` afterwards; Odoo stock fell by the sold quantity (earlier test with the previous transport) |
| Duplicates | Re-sending the same reference returns the existing picking and creates no second one |
| Not yet verified live | Replenishment (internal transfer); switching between two real Odoo databases |
| Odoo 19 differences handled | `stock.move` has no `name` field; a method that returns nothing answers `null`; `display_name` carries a `[SKU]` prefix that is stripped |

### 8.4 Outbox Worker

A separate process (`python -m app.worker`) polls `integration_events` where status is PENDING and `next_attempt_at <= now()`, using `FOR UPDATE SKIP LOCKED` so multiple workers do not double-process. For each event it calls the adapter with the event's `idempotency_key` as the Odoo reference, then sets DONE and updates `sales.odoo_status` or `replenishment_requests.status`.

Error handling:

| Failure | Handling |
|---------|----------|
| Timeout, connection error, HTTP 5xx | Retry with exponential backoff (up to 5 attempts), then FAILED |
| Business rejection (unknown product, insufficient stock in Odoo) | FAILED immediately, no retry, `last_error` recorded |
| Worker crash mid-call | Event stays PROCESSING past a timeout and is re-queued. Safe because of the duplicate protection below |

Exact backoff values are left to the builder.

### 8.5 Avoiding Duplicate Odoo Deductions

1. One event per request (unique `idempotency_key`, which is the request type plus `request_id`, so sales and replenishment requests can never collide).
2. The Odoo reference is `SALE:<request_id>` (or `REPLENISHMENT:<request_id>` for transfers; the type prefix keeps the two request-id namespaces apart). Before creating a picking, the live client searches for an existing picking with that `origin`; if found it returns that picking instead of creating another.
3. The mock rejects a repeated reference and returns the original result, mirroring step 2.

Odoo has no native idempotency key, so step 2 is a check-then-create. It is safe in practice because a single worker holds the event row lock, but it is not enforced by an Odoo-side unique constraint (see 8.6).

### 8.6 Custom Odoo Module (deferred)

If duplicate protection must be enforced inside Odoo, a small module adding a unique constraint on an external reference would live in `odoo-module/`. It is not built unless the live instance shows this need.

### 8.7 Stock Synchronisation

`POST /api/inventory/sync` pulls master data and stock from Odoo. The projection is set to Odoo's quantity minus the quantity of sale events still PENDING, PROCESSING or FAILED for that (variant, location), so unsent local sales are not overwritten. **[Assumption]** The sync writes SYNC_ADJUSTMENT movements when quantities change.

On first start the local tables are empty and are seeded by running the same sync once (mock: sample data, damaged quantities 0). After that, sync runs only when triggered through the endpoint or the dashboard refresh button; there is no scheduled sync. **[Assumption]**

Known window: if an event completes between reading Odoo stock and computing pending events, the projection can be briefly high (self-corrects on the next sync). Documented as a hardening item.

If Odoo later rejects a sale that was already accepted locally (projection was ahead), the event becomes FAILED and requires an operator decision. There is no automatic reversal. This is the trade-off of the outbox pattern.

### 8.8 Mock Odoo

Class seeded with the sample data, keyed by reference for deduplication, with switchable failure injection (timeout, 5xx, business error) so retry behaviour can be demonstrated. State is stored in a separate PostgreSQL schema (`mock_odoo`) rather than in memory, because the API and the outbox worker are separate processes and must see the same simulated Odoo. It is reachable only through the mock client and stands in for Odoo's own database. **[Assumption]** (revised during planning)

## 9. REST API

Base path `/api`. JSON. Errors use one shape: `{"error": {"code": "...", "message": "...", "details": ...}}`.

| Method and path | Purpose | Success | Errors |
|-----------------|---------|---------|--------|
| `GET /api/inventory?store=&q=&status=` | Variants with stock per location (store filter optional) | 200 | 422 invalid filter, 404 unknown store |
| `GET /api/inventory/{variant_id}/movements?limit=` | Recent movements for the drawer | 200 | 404 |
| `POST /api/inventory/sync` | Refresh projection from Odoo | 200 | 502 Odoo unavailable |
| `POST /api/sales` | Record a sale (section 7) | 201, 200 on replay | 404 unknown variant/store, 409 `INSUFFICIENT_STOCK` or `IDEMPOTENCY_KEY_REUSED`, 422 |
| `GET /api/replenishment` | Store/variant pairs below threshold, with current quantity, threshold, warehouse availability and suggested quantity | 200 | |
| `POST /api/replenishment/request` | Create a request: `request_id`, `store_id`, `variant_id`, `quantity` | 201, 200 on replay | 404, 409 key reused, 422 (store must be a STORE) |
| `GET /api/integration-events?status=` | Operational view of outbox events (troubleshooting) | 200 | |
| `POST /api/integration-events/{id}/retry` | Re-queue a FAILED event (sets PENDING, resets attempts) | 200 | 404, 409 if not FAILED |
| `GET /health` | Liveness and database check | 200 | 503 |

Notes:

- Suggested replenishment quantity is the amount needed to reach twice the threshold. **[Assumption]**
- Quantity validation: integer, minimum 1, maximum 1000. **[Assumption]**
- Inventory responses carry `Cache-Control: no-store` and an `as_of` timestamp.
- Replenishment requests follow the same idempotency and outbox pattern as sales. Status moves PENDING to SUBMITTED once Odoo confirms the transfer, or FAILED.
- A replenishment request does not move or reserve stock in the local projection and does not check warehouse availability. Quantities change only when the next sync picks up the completed Odoo transfer. A second request for the same store/variant is rejected with HTTP 409 `OPEN_REQUEST_EXISTS` while one is PENDING. `GET /api/replenishment` flags pairs that already have a PENDING request. **[Assumption]**
- Store identifiers in the API (`store` query parameter, `store_id` in bodies) are location codes (`SHOP1`, `SHOP2`, `WH`). Variants are identified by numeric `variant_id`. **[Assumption]**

## 10. Backend Structure

```
backend/
  app/
    main.py            # app factory, error handlers, logging setup
    config.py          # environment settings
    routers/           # HTTP only: parse, call service, shape response
    services/          # business rules (sales, inventory, replenishment, sync)
    repositories/      # all SQL
    schemas/           # Pydantic request/response models
    odoo/              # OdooClient interface, mock, live
    worker.py          # outbox worker entry point
  migrations/          # Alembic
  tests/
```

SQLAlchemy 2.0 with psycopg and synchronous endpoints (FastAPI runs them in a thread pool). **[Assumption]** Logging uses the standard library with key=value lines carrying `request_id`, event id and Odoo reference.

## 11. Frontend (`frontend/`)

- Single dashboard page. Table of variants with a column per location showing available stock and a status badge. Search box, store filter and status filter.
- Detail drawer: stock by location (including damaged), recent movements, and a small "record sale" form so that a sale can be sent from the UI and the refresh observed. **[Assumption]**
- A replenishment panel listing items below threshold with a "request" action. **[Assumption]**
- Data fetching with TanStack Query. **[Assumption]** Short stale time, polling refetch, a manual refresh button, and cache invalidation after any mutation. This is the answer to the stale-cache interview question: mutations invalidate the inventory queries, and `no-store` on the API prevents intermediate caching.
- States: loading skeleton, empty result, error with retry, and a visible "last updated" time.
- Responsive layout (table collapses to cards on narrow screens).

## 12. Testing

| Test | Type | Verifies |
|------|------|----------|
| Sale service unit test | Unit | Validation, location resolution (POS vs ONLINE), insufficient stock, idempotent replay and payload mismatch |
| Sales API test | Integration (real PostgreSQL test database) | Request to response, error shapes, stock and outbox rows written |
| Last-unit concurrency test | Integration | Section 7.2 |
| Outbox worker test against mock | Integration | Success, retry on transient failure, FAILED on business rejection, no duplicate deduction |

Commands will be documented in the README (`pytest` for the backend). A frontend test is optional.

## 13. Troubleshooting, Operations, Hardening

### 13.1 Investigation Playbooks

| Symptom | Approach |
|---------|----------|
| Stale inventory after a POS sale | Check the sale's `odoo_status` and its outbox event (`GET /api/integration-events`). Check the worker is running and `next_attempt_at`. Compare projection with Odoo through a sync. Check the browser was invalidated or the response was cached (`as_of`, cache headers). |
| Duplicate transactions | Search by `request_id`. The unique constraint makes duplicates in `sales` impossible, so look for different keys sent for one intent (client retry logic) or duplicate Odoo pickings by `origin`. |
| Failed replenishment requests | Read `last_error` and `attempts` on the event. Transient (retry or restore Odoo connectivity) versus business rejection (fix master data or mapping, then re-queue). |

### 13.2 Monitoring and Logging

Structured log lines with correlation identifiers. Metrics to track: outbox backlog size and oldest pending age, FAILED event count, Odoo call latency and error rate, 409 rate on sales, and sync drift between projection and Odoo. Alert on backlog age and any FAILED event.

### 13.3 Deployment (documented, not built)

Containerise backend, worker and frontend separately, run migrations as a release step, store the Odoo API key in a secret manager, and run the worker as its own deployment with horizontal scaling enabled by `SKIP LOCKED`.

### 13.4 Hardening and Scaling (two stores to 100)

Add authentication and store-scoped authorisation, rate limiting, an Odoo-side unique reference (see 8.6), reconciliation job with drift alerts, and a dead-letter review screen. For scale: the sale path is already row-level, so contention only exists per (variant, location). Move sync to incremental, add read replicas or caching for the dashboard, partition `stock_movements` and `integration_events`, and introduce a broker if polling becomes a bottleneck.

## 14. Deliverables

Runnable `frontend/` and `backend/`, Alembic migrations and seed, README with setup, run and test commands and example requests, and this document. The README also lists incomplete items: no end-user authentication, no public damaged-return endpoint, in-memory mock state, Docker not provided, and the sync window in 8.7.

## 15. Left to the Builder

Exact backoff timings, UI styling, folder-internal naming, choice of component library, and migration granularity, provided the behaviour above is preserved.
