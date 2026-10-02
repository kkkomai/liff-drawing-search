# Bento Order API (backend)

LINE LIFF お弁当注文システム — FastAPI + SQLite backend implementing the API
contract in `../bento_liff_spec.md` (task 0). All dates are Asia/Tokyo.

## Run

```bash
# one-time setup
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env

# create the employee master (20 people, E001 = admin)
./.venv/Scripts/python.exe scripts/seed.py --count 20 --admin-code E001

# serve
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Migrations run automatically at startup (`app/migrations/NNN_*.sql`, tracked in
`schema_migrations`), so a fresh database self-initialises.

## Verify

```bash
# 62 unit/integration tests (TestClient, isolated temp DB per test)
./run_tests.bat
# or:  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/Scripts/python.exe -m pytest

# live end-to-end against a real uvicorn process on a real port
./.venv/Scripts/python.exe scripts/smoke_e2e.py
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` works around a machine-wide `langsmith`
pytest plugin that shadows `httpx` and breaks collection; `run_tests.bat` sets
it for you.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | — | verify LINE ID token → session bearer token + employee |
| POST | `/api/auth/logout` | bearer | revoke the current session |
| GET | `/api/auth/me` | bearer | current employee + server time |
| GET | `/api/orders?start_date&end_date` | bearer | own orders in a period |
| POST/PUT | `/api/orders` | bearer | upsert `{date, status}` (today or future only) |
| DELETE | `/api/orders/{date}` | bearer | clear a today/future entry (audit-logged) |
| GET | `/api/orders/audit` | bearer | own change history |
| GET | `/api/admin/orders?date=` | admin | all active employees for one day |
| GET | `/api/admin/orders?start_date=&end_date=` | admin | per-day rollup for a period (≤62 days) |
| GET | `/api/admin/summary` | admin | today's counts |
| GET | `/api/admin/audit?date=` | admin | change history across employees |
| GET | `/health` | — | liveness + which auth modes are active |

Errors always use one shape:

```json
{"success": false, "error": "past_date_modification_not_allowed", "message": "..."}
```

## Design decisions

**Auth.** `POST /api/auth/login` verifies the LIFF `id_token` (RS256) against
LINE's JWKS, checking `kid`, `iss`, `aud` (= channel id) and `exp`, then requires
the `sub` claim to match the submitted `line_user_id` — so a client cannot
present someone else's ID. Only a `line_user_id` present in `employees` gets a
session; everyone else gets `403 unauthorized_user` (spec 6.1). If neither the
LINE credentials nor `BENTO_DEV_AUTH_TOKEN` are configured the endpoint fails
closed (401/503), it never falls back to trusting a bare `line_user_id`.

**Sessions.** Opaque 32-byte tokens; only SHA-256 hashes are stored, so a
database leak yields no usable credentials. Tokens carry an expiry and can be
revoked via `/api/auth/logout`.

**One employee, one day.** Enforced by `UNIQUE(employee_id, date)`, not just by
application code — a regression test asserts the constraint rejects a second
row. Writes take `BEGIN IMMEDIATE`, so simultaneous taps serialise; the same
choice sent twice is a no-op (no duplicate row, no audit noise), while a changed
choice becomes an UPDATE plus an audit entry.

**Past dates are read-only.** `date < today (Asia/Tokyo)` is rejected with
`400 past_date_modification_not_allowed` for both update and delete. The check
lives in the service layer, so a tampered LIFF client cannot rewrite history —
a test stores a row with a past date and confirms the API still refuses it.
Future dates have no upper bound (spec 4.3).

**History is never pruned.** No TTL or retention job exists; `GET /api/orders`
reads any past date the client asks for (capped at 400 days per request so one
call cannot ask for 20 years). Defaults with no range: 330 days back, 60 ahead.

**Audit trail.** Every create/update/delete appends to `order_audit_logs` with
`old_status`, `new_status`, `changed_at`, `changed_by` (employee code) and
`action`, in the same transaction as the write — so the history cannot drift
from the data. Employees read their own trail, admins read everyone's.

**`X-Line-User-Id` is deliberately not implemented.** The spec lists it as an
alternative to the bearer token, but any client can set that header, so
accepting it would let anyone read or write any employee's orders by guessing an
ID. Only the session bearer token is accepted; `id_token` verification is the
real trust boundary.

**Config.** Everything deployment-specific is environment-driven with the
`BENTO_` prefix (`app/config.py`); nothing is hard-coded.

## Known limitations

- SQLite + a single process. Multi-worker uvicorn on SQLite works (WAL) but
  PostgreSQL is the right choice if writes grow; the service layer is the only
  place that would change.
- Admin period listing is capped at 62 days to bound response size.
- No LINE Messaging API outbound push yet (that's the integration task).