-- 002: admin page authentication (管理ページ /admin.html 用) — Postgres variant
--
-- LIFF アプリとは別のブラウザから開く管理ページの認証基盤。
-- employees (LIFFログイン) とは独立した admin_users テーブルで、
-- パスワードは pbkdf2 ハッシュで保存する。
-- セッションは admin_sessions にトークンの SHA-256 ハッシュのみ保存
-- (生トークンは保存しない = DB漏洩してもログイン不能)。

CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name VARCHAR(50) NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id SERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    admin_user_id INTEGER NOT NULL REFERENCES admin_users (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_user ON admin_sessions (admin_user_id);
