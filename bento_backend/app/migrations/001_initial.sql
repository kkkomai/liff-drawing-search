-- 001: initial schema (spec section 3)
-- Timezone reference for every DATE/DATETIME value is Asia/Tokyo.

CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_code VARCHAR(20)  NOT NULL UNIQUE,
    name          VARCHAR(50)  NOT NULL,
    line_user_id  VARCHAR(100) UNIQUE,
    role          VARCHAR(20)  NOT NULL DEFAULT 'employee'
                  CHECK (role IN ('employee', 'admin')),
    is_active     INTEGER      NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT         NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_employees_active ON employees (is_active);

CREATE TABLE IF NOT EXISTS bento_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER     NOT NULL REFERENCES employees (id) ON DELETE CASCADE,
    date        TEXT        NOT NULL,            -- YYYY-MM-DD (Asia/Tokyo)
    status      TEXT        NOT NULL CHECK (status IN ('needed', 'not_needed')),
    created_at  TEXT        NOT NULL,
    updated_at  TEXT        NOT NULL,
    updated_by  TEXT        NOT NULL,
    CONSTRAINT uq_bento_orders_employee_date UNIQUE (employee_id, date)
);

CREATE INDEX IF NOT EXISTS idx_bento_orders_date ON bento_orders (date);

CREATE TABLE IF NOT EXISTS order_audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL,
    employee_id INTEGER NOT NULL REFERENCES employees (id) ON DELETE CASCADE,
    date        TEXT    NOT NULL,
    old_status  TEXT,
    new_status  TEXT    NOT NULL,
    changed_at  TEXT    NOT NULL,
    changed_by  TEXT    NOT NULL,
    action      TEXT    NOT NULL CHECK (action IN ('create', 'update', 'delete'))
);

CREATE INDEX IF NOT EXISTS idx_audit_employee_date
    ON order_audit_logs (employee_id, date);

-- Sessions issued by POST /api/auth/login (opaque bearer tokens).
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees (id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    revoked_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_employee ON sessions (employee_id);