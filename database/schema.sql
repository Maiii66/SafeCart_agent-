-- The audit trail: every decision the agent or the human makes is written here.
CREATE TABLE IF NOT EXISTS action_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    actor   TEXT NOT NULL,          -- 'agent', 'human' or 'guardrail'
    action  TEXT NOT NULL,          -- short code, e.g. 'blocked_over_budget'
    detail  TEXT NOT NULL,          -- plain-English explanation
    ref     TEXT                    -- optional: product or order id
);
