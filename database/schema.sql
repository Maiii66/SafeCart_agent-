-- The audit trail: every decision the agent or the human makes is written here.
CREATE TABLE IF NOT EXISTS action_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    actor   TEXT NOT NULL,          -- 'agent', 'human' or 'guardrail'
    action  TEXT NOT NULL,          -- short code, e.g. 'blocked_over_budget'
    detail  TEXT NOT NULL,          -- plain-English explanation
    ref     TEXT                    -- optional: product or order id
);

-- Phase 2 tables
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    step_up_confirmed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id TEXT NOT NULL,
    paypal_order_id TEXT,
    status TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    captured_at INTEGER,
    capture_id TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
