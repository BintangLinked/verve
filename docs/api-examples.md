# API examples

Generated from a real run on 2026-11-13 against `ODOO_MODE=mock` with freshly seeded data. Every response below is actual output.
The commands assume the API on `localhost:8000` and are run from the `backend/` directory. Calls to `/api/integration-events` need the administrator session cookie (`-b cookies.txt`, created by signing in as shown in the last section). Variant ids after a fresh seed: 1 = Black / L, 2 = Black / M, 3 = White / L, 4 = Serum, 5 = Tote bag.

## Health check

```bash
curl localhost:8000/health
```

Response:

```
{
  "status": "ok"
}
HTTP 200
```

## Search inventory

```bash
curl "localhost:8000/api/inventory?q=serum"
```

Response:

```
{
  "as_of": "2026-11-13T09:17:09.313572Z",
  "items": [
    {
      "variant_id": 4,
      "sku": "SERUM-50",
      "product_name": "Hydrating Face Serum",
      "variant_name": "50 ml",
      "threshold": 6,
      "stock": [
        {
          "location_code": "WH",
          "available": 50,
          "damaged": 0,
          "status": null
        },
        {
          "location_code": "SHOP1",
          "available": 20,
          "damaged": 0,
          "status": "OK"
        },
        {
          "location_code": "SHOP2",
          "available": 8,
          "damaged": 0,
          "status": "OK"
        }
      ]
    }
  ]
}
HTTP 200
```

## Low stock at one store

```bash
curl "localhost:8000/api/inventory?store=SHOP2&status=LOW"
```

Response:

```
{
  "as_of": "2026-11-13T09:17:09.372786Z",
  "items": [
    {
      "variant_id": 3,
      "sku": "TS-WHT-L",
      "product_name": "Cotton T-Shirt",
      "variant_name": "White / L",
      "threshold": 5,
      "stock": [
        {
          "location_code": "SHOP2",
          "available": 4,
          "damaged": 0,
          "status": "LOW"
        }
      ]
    }
  ]
}
HTTP 200
```

## Recent stock movements (2 newest)

```bash
curl "localhost:8000/api/inventory/3/movements?limit=2"
```

Response:

```
[
  {
    "id": 9,
    "location_code": "WH",
    "bucket": "sellable",
    "type": "SYNC_ADJUSTMENT",
    "qty_delta": 20,
    "ref_type": "SYNC",
    "ref_id": null,
    "created_at": "2026-11-13T16:17:08.566417+07:00"
  },
  {
    "id": 8,
    "location_code": "SHOP2",
    "bucket": "sellable",
    "type": "SYNC_ADJUSTMENT",
    "qty_delta": 4,
    "ref_type": "SYNC",
    "ref_id": null,
    "created_at": "2026-11-13T16:17:08.566417+07:00"
  }
]
HTTP 200
```

## POS sale (201 Created)

```bash
curl -X POST localhost:8000/api/sales -H 'content-type: application/json' -d '{"request_id":"sale-0001","variant_id":1,"quantity":2,"channel":"POS","store_id":"SHOP1"}'
```

Response:

```
{
  "id": 1,
  "request_id": "sale-0001",
  "variant_id": 1,
  "location_code": "SHOP1",
  "quantity": 2,
  "channel": "POS",
  "remaining": 6,
  "odoo_status": "PENDING",
  "replayed": false
}
HTTP 201
```

## Same request retried: replayed, no second sale (200)

```bash
curl -X POST localhost:8000/api/sales -H 'content-type: application/json' -d '{"request_id":"sale-0001","variant_id":1,"quantity":2,"channel":"POS","store_id":"SHOP1"}'
```

Response:

```
{
  "id": 1,
  "request_id": "sale-0001",
  "variant_id": 1,
  "location_code": "SHOP1",
  "quantity": 2,
  "channel": "POS",
  "remaining": 6,
  "odoo_status": "PENDING",
  "replayed": true
}
HTTP 200
```

## Insufficient stock (409)

```bash
curl -X POST localhost:8000/api/sales -H 'content-type: application/json' -d '{"request_id":"sale-0002","variant_id":3,"quantity":99,"channel":"POS","store_id":"SHOP2"}'
```

Response:

```
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Not enough sellable stock",
    "details": {
      "available": 4,
      "requested": 99
    }
  }
}
HTTP 409
```

## Same request_id, different payload (409)

```bash
curl -X POST localhost:8000/api/sales -H 'content-type: application/json' -d '{"request_id":"sale-0001","variant_id":1,"quantity":3,"channel":"POS","store_id":"SHOP1"}'
```

Response:

```
{
  "error": {
    "code": "IDEMPOTENCY_KEY_REUSED",
    "message": "request_id was already used with a different payload",
    "details": null
  }
}
HTTP 409
```

## Invalid quantity (422)

```bash
curl -X POST localhost:8000/api/sales -H 'content-type: application/json' -d '{"request_id":"sale-0003","variant_id":1,"quantity":0,"channel":"POS","store_id":"SHOP1"}'
```

Response:

```
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {
        "type": "greater_than_equal",
        "loc": [
          "body",
          "quantity"
        ],
        "msg": "Input should be greater than or equal to 1",
        "input": 0,
        "ctx": {
          "ge": 1
        }
      }
    ]
  }
}
HTTP 422
```

## POS sale without a store (422)

```bash
curl -X POST localhost:8000/api/sales -H 'content-type: application/json' -d '{"request_id":"sale-0004","variant_id":1,"quantity":1,"channel":"POS"}'
```

Response:

```
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {
        "type": "value_error",
        "loc": [
          "body"
        ],
        "msg": "Value error, store_id is required for POS sales",
        "input": {
          "request_id": "sale-0004",
          "variant_id": 1,
          "quantity": 1,
          "channel": "POS"
        },
        "ctx": {
          "error": {}
        }
      }
    ]
  }
}
HTTP 422
```

## Online sale is fulfilled from the main warehouse (201)

```bash
curl -X POST localhost:8000/api/sales -H 'content-type: application/json' -d '{"request_id":"sale-0005","variant_id":2,"quantity":1,"channel":"ONLINE"}'
```

Response:

```
{
  "id": 5,
  "request_id": "sale-0005",
  "variant_id": 2,
  "location_code": "WH",
  "quantity": 1,
  "channel": "ONLINE",
  "remaining": 24,
  "odoo_status": "PENDING",
  "replayed": false
}
HTTP 201
```

## Items below threshold

```bash
curl localhost:8000/api/replenishment
```

Response:

```
[
  {
    "store_code": "SHOP2",
    "variant_id": 3,
    "sku": "TS-WHT-L",
    "variant_name": "White / L",
    "available": 4,
    "threshold": 5,
    "status": "LOW",
    "warehouse_available": 20,
    "suggested_quantity": 6,
    "pending_request": false
  }
]
HTTP 200
```

## Create a replenishment request (201)

```bash
curl -X POST localhost:8000/api/replenishment/request -H 'content-type: application/json' -d '{"request_id":"rep-0001","store_id":"SHOP2","variant_id":3,"quantity":6}'
```

Response:

```
{
  "id": 1,
  "request_id": "rep-0001",
  "store_id": "SHOP2",
  "variant_id": 3,
  "quantity": 6,
  "status": "PENDING",
  "replayed": false
}
HTTP 201
```

## Second pending request for the same store and variant (409)

```bash
curl -X POST localhost:8000/api/replenishment/request -H 'content-type: application/json' -d '{"request_id":"rep-0002","store_id":"SHOP2","variant_id":3,"quantity":6}'
```

Response:

```
{
  "error": {
    "code": "OPEN_REQUEST_EXISTS",
    "message": "A pending request already exists for this store and variant",
    "details": null
  }
}
HTTP 409
```

## Outbox events waiting to be sent to Odoo

```bash
curl -b cookies.txt "localhost:8000/api/integration-events?status=PENDING"
```

Response:

```
[
  {
    "id": 3,
    "event_type": "REPLENISHMENT",
    "aggregate_id": 1,
    "idempotency_key": "REPLENISHMENT:rep-0001",
    "status": "PENDING",
    "attempts": 0,
    "next_attempt_at": "2026-11-13T16:17:10.275723+07:00",
    "last_error": null,
    "created_at": "2026-11-13T16:17:10.275723+07:00",
    "processed_at": null
  },
  {
    "id": 2,
    "event_type": "SALE",
    "aggregate_id": 5,
    "idempotency_key": "SALE:sale-0005",
    "status": "PENDING",
    "attempts": 0,
    "next_attempt_at": "2026-11-13T16:17:10.082400+07:00",
    "last_error": null,
    "created_at": "2026-11-13T16:17:10.082400+07:00",
    "processed_at": null
  },
  {
    "id": 1,
    "event_type": "SALE",
    "aggregate_id": 1,
    "idempotency_key": "SALE:sale-0001",
    "status": "PENDING",
    "attempts": 0,
    "next_attempt_at": "2026-11-13T16:17:09.560510+07:00",
    "last_error": null,
    "created_at": "2026-11-13T16:17:09.560510+07:00",
    "processed_at": null
  }
]
HTTP 200
```

## Events after the worker ran

```bash
curl -b cookies.txt "localhost:8000/api/integration-events?status=DONE"
```

Response:

```
[
  {
    "id": 3,
    "event_type": "REPLENISHMENT",
    "aggregate_id": 1,
    "idempotency_key": "REPLENISHMENT:rep-0001",
    "status": "DONE",
    "attempts": 0,
    "next_attempt_at": "2026-11-13T16:17:18.086224+07:00",
    "last_error": null,
    "created_at": "2026-11-13T16:17:10.275723+07:00",
    "processed_at": "2026-11-13T16:17:18.086224+07:00"
  },
  {
    "id": 2,
    "event_type": "SALE",
    "aggregate_id": 5,
    "idempotency_key": "SALE:sale-0005",
    "status": "DONE",
    "attempts": 0,
    "next_attempt_at": "2026-11-13T16:17:18.071608+07:00",
    "last_error": null,
    "created_at": "2026-11-13T16:17:10.082400+07:00",
    "processed_at": "2026-11-13T16:17:18.071608+07:00"
  },
  {
    "id": 1,
    "event_type": "SALE",
    "aggregate_id": 1,
    "idempotency_key": "SALE:sale-0001",
    "status": "DONE",
    "attempts": 0,
    "next_attempt_at": "2026-11-13T16:17:18.037468+07:00",
    "last_error": null,
    "created_at": "2026-11-13T16:17:09.560510+07:00",
    "processed_at": "2026-11-13T16:17:18.037468+07:00"
  }
]
HTTP 200
```

## Sync the projection from Odoo (picks up the completed transfer)

```bash
curl -X POST localhost:8000/api/inventory/sync
```

Response:

```
{
  "variants": 5,
  "locations": 3,
  "adjustments": 2
}
HTTP 200
```

## White / L after the sync: Store 2 rose from 4 to 10

```bash
curl "localhost:8000/api/inventory?q=white"
```

Response:

```
{
  "as_of": "2026-11-13T09:17:19.307280Z",
  "items": [
    {
      "variant_id": 3,
      "sku": "TS-WHT-L",
      "product_name": "Cotton T-Shirt",
      "variant_name": "White / L",
      "threshold": 5,
      "stock": [
        {
          "location_code": "WH",
          "available": 14,
          "damaged": 0,
          "status": null
        },
        {
          "location_code": "SHOP1",
          "available": 6,
          "damaged": 0,
          "status": "OK"
        },
        {
          "location_code": "SHOP2",
          "available": 10,
          "damaged": 0,
          "status": "OK"
        }
      ]
    }
  ]
}
HTTP 200
```

## Recovering from a failed Odoo delivery

Before the next three calls, the mock Odoo was told to reject one operation (`fail_next('business')`, see README section 7.3) and a sale was recorded and sent by the worker.

## List FAILED events

```bash
curl -b cookies.txt "localhost:8000/api/integration-events?status=FAILED"
```

Response:

```
[
  {
    "id": 4,
    "event_type": "SALE",
    "aggregate_id": 6,
    "idempotency_key": "SALE:sale-0006",
    "status": "FAILED",
    "attempts": 1,
    "next_attempt_at": "2026-11-13T16:17:32.455742+07:00",
    "last_error": "OdooBusinessError: mock: injected business failure",
    "created_at": "2026-11-13T16:17:24.651152+07:00",
    "processed_at": "2026-11-13T16:17:32.455742+07:00"
  }
]
HTTP 200
```

## Re-queue the failed event once the cause is fixed

```bash
curl -b cookies.txt -X POST localhost:8000/api/integration-events/4/retry
```

Response:

```
{
  "id": 4,
  "event_type": "SALE",
  "aggregate_id": 6,
  "idempotency_key": "SALE:sale-0006",
  "status": "PENDING",
  "attempts": 0,
  "next_attempt_at": "2026-11-13T16:17:33.449427+07:00",
  "last_error": null,
  "created_at": "2026-11-13T16:17:24.651152+07:00",
  "processed_at": null
}
HTTP 200
```

## The event after the worker retried it

```bash
curl -b cookies.txt "localhost:8000/api/integration-events?limit=1"
```

Response:

```
[
  {
    "id": 4,
    "event_type": "SALE",
    "aggregate_id": 6,
    "idempotency_key": "SALE:sale-0006",
    "status": "DONE",
    "attempts": 0,
    "next_attempt_at": "2026-11-13T16:17:41.353710+07:00",
    "last_error": null,
    "created_at": "2026-11-13T16:17:24.651152+07:00",
    "processed_at": "2026-11-13T16:17:41.353710+07:00"
  }
]
HTTP 200
```


## Odoo connection (administrator only)

These calls were captured from a real run on 2026-11-13 against `ODOO_MODE=mock` with a temporary administrator account. The session cookie is shown redacted. Create the login with `python -m app.hash_password` (README section 3.2).

### Without a session (401)

```bash
curl -i localhost:8000/api/odoo/connection
```

```
GET /api/odoo/connection -> 401
```

### Wrong password (401, identical for an unknown user)

```bash
curl -X POST localhost:8000/api/auth/login -H 'content-type: application/json' -d '{"username":"admin","password":"wrong"}'
```

```
{"error":{"code":"INVALID_CREDENTIALS","message":"Invalid username or password","details":null}} -> 401
```

### Sign in (200, httpOnly cookie)

```bash
curl -c cookies.txt -i -X POST localhost:8000/api/auth/login -H 'content-type: application/json' -d '{"username":"admin","password":"..."}'
```

```
HTTP/1.1 200 OK
set-cookie: verve_session=<redacted>; HttpOnly; Max-Age=3600; Path=/; SameSite=lax
```

### Stored status (200; does not call Odoo)

```bash
curl -b cookies.txt localhost:8000/api/odoo/connection
```

```
{"mode":"mock","status":"connected","database":"mock","odoo_version":"mock","last_checked_at":"2026-11-13T08:53:16.369859Z","error":null}
```

### Test the connection (200; a request body, if sent, is ignored)

```bash
curl -b cookies.txt -X POST localhost:8000/api/odoo/connection/test
```

```
{"mode":"mock","status":"connected","database":"mock","odoo_version":"mock","last_checked_at":"2026-11-13T08:53:17.558316Z","error":null}
```

### Sign out (204), then the same request is refused (401)

```bash
curl -b cookies.txt -c cookies.txt -X POST localhost:8000/api/auth/logout
curl -b cookies.txt localhost:8000/api/odoo/connection
```

```
logout -> 204
GET after logout -> 401
```

With `ODOO_MODE=live`, a failed check returns `"status":"disconnected"` and an error such as `{"code":"DATABASE_NOT_FOUND","message":"The configured database was not found on the Odoo server."}`. The live responses have not been captured because no Odoo instance exists yet.

## Iteration 3: login required, Odoo configuration and password change

Since iteration 3 every endpoint except `/health` and `POST /api/auth/login` requires the administrator session. Add `-b cookies.txt` (created by signing in, see the section above) to the earlier inventory, sales and replenishment examples. Without a session they answer `401` with code `UNAUTHENTICATED`.

```bash
curl -b cookies.txt localhost:8000/api/odoo/config
```

Response (the API key is never returned, only whether one is stored):

```
{"source":"database","configured":true,"url":"http://localhost:9090","database":"verve_database","api_key_set":true}
```

```bash
curl -b cookies.txt -X PUT localhost:8000/api/odoo/config -H 'content-type: application/json' \
  -d '{"url":"http://localhost:9090","database":"verve_database","api_key":"<api key; empty keeps the stored one for the same server and database>"}'
```

Errors: `422 URL_NOT_ALLOWED` (SSRF policy), `422 API_KEY_REQUIRED` (first save, or a different server or database, without a key), `409 ODOO_SWITCH_REQUIRES_RESET` (different Odoo while local data exists; resend with `"reset_local_data":true` to delete that data and reload from the new Odoo), `503 ENCRYPTION_NOT_CONFIGURED` (no `SETTINGS_ENCRYPTION_KEY` on the server).

Disconnect (deletes the stored URL, database and API key; the response has the same shape as `GET`):

```bash
curl -b cookies.txt -X DELETE localhost:8000/api/odoo/config
```

```bash
curl -b cookies.txt -X PUT localhost:8000/api/auth/password -H 'content-type: application/json' \
  -d '{"current_password":"<current>","new_password":"<at least 12 characters>"}'
```

Response `204`; every session, including this one, ends. A wrong current password answers `400 INVALID_CURRENT_PASSWORD`.
