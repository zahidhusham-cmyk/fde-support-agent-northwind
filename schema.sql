-- Northwind Mobile: carrier world + customer devices + support tickets.
-- SQLite. Booleans are INTEGER 0/1. Money is integer cents. Data is integer MB.

PRAGMA foreign_keys = ON;

-- Plans the carrier sells (reference data).
CREATE TABLE plans (
    id                  TEXT PRIMARY KEY,           -- e.g. 'PLN-BASIC'
    name                TEXT NOT NULL,
    monthly_price_cents INTEGER NOT NULL CHECK (monthly_price_cents >= 0),
    data_cap_mb         INTEGER NOT NULL CHECK (data_cap_mb > 0),
    roaming_included    INTEGER NOT NULL DEFAULT 0 CHECK (roaming_included IN (0, 1))
);

-- Account holders.
CREATE TABLE customers (
    id            TEXT PRIMARY KEY,                 -- e.g. 'CUS-1001'
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    phone_contact TEXT NOT NULL,
    date_of_birth TEXT NOT NULL,                    -- ISO date, used for identity checks
    account_pin   TEXT NOT NULL,                    -- 4 digits, fictional, used for identity checks
    created_at    TEXT NOT NULL
);

-- A phone number (SIM) on the network. Carrier-side state the AGENT can see/change.
CREATE TABLE lines (
    id                TEXT PRIMARY KEY,             -- e.g. 'LIN-2001'
    customer_id       TEXT NOT NULL REFERENCES customers(id),
    msisdn            TEXT NOT NULL UNIQUE,         -- the phone number
    plan_id           TEXT NOT NULL REFERENCES plans(id),
    status            TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'cancelled')),
    suspension_reason TEXT CHECK (suspension_reason IN ('non_payment', 'fraud_hold', 'customer_request')),
    data_used_mb      INTEGER NOT NULL DEFAULT 0 CHECK (data_used_mb >= 0),  -- this billing cycle
    data_topup_mb     INTEGER NOT NULL DEFAULT 0 CHECK (data_topup_mb >= 0), -- extra data bought this cycle
    provisioning_ok   INTEGER NOT NULL DEFAULT 1 CHECK (provisioning_ok IN (0, 1)), -- network profile pushed correctly
    roaming_enabled   INTEGER NOT NULL DEFAULT 0 CHECK (roaming_enabled IN (0, 1)),  -- carrier-side roaming permission
    CHECK ((status = 'suspended') = (suspension_reason IS NOT NULL))
);

-- Monthly bills per customer.
CREATE TABLE bills (
    id           TEXT PRIMARY KEY,                  -- e.g. 'BIL-3001'
    customer_id  TEXT NOT NULL REFERENCES customers(id),
    period       TEXT NOT NULL,                     -- 'YYYY-MM'
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    due_date     TEXT NOT NULL,
    paid_at      TEXT,                              -- NULL = unpaid
    overdue      INTEGER NOT NULL DEFAULT 0 CHECK (overdue IN (0, 1)),
    UNIQUE (customer_id, period),
    CHECK (NOT (paid_at IS NOT NULL AND overdue = 1))
);

-- The customer's handset. Device-side state only the USER can see/change.
CREATE TABLE devices (
    id                  TEXT PRIMARY KEY,           -- e.g. 'DEV-4001'
    line_id             TEXT NOT NULL UNIQUE REFERENCES lines(id),
    model               TEXT NOT NULL,
    os                  TEXT NOT NULL CHECK (os IN ('android', 'ios')),
    airplane_mode       INTEGER NOT NULL DEFAULT 0 CHECK (airplane_mode IN (0, 1)),
    mobile_data_enabled INTEGER NOT NULL DEFAULT 1 CHECK (mobile_data_enabled IN (0, 1)),
    data_roaming_on     INTEGER NOT NULL DEFAULT 0 CHECK (data_roaming_on IN (0, 1)), -- phone setting
    is_abroad           INTEGER NOT NULL DEFAULT 0 CHECK (is_abroad IN (0, 1)),       -- where the customer physically is
    apn_ok              INTEGER NOT NULL DEFAULT 1 CHECK (apn_ok IN (0, 1)),          -- APN settings correct on handset
    needs_restart       INTEGER NOT NULL DEFAULT 0 CHECK (needs_restart IN (0, 1)),   -- stale radio state, cleared by a reboot
    hardware_fault      INTEGER NOT NULL DEFAULT 0 CHECK (hardware_fault IN (0, 1)),  -- nothing remote can fix this
    signal_bars         INTEGER NOT NULL DEFAULT 4 CHECK (signal_bars BETWEEN 0 AND 5)
);

-- Support tickets.
CREATE TABLE tickets (
    id          TEXT PRIMARY KEY,                   -- e.g. 'TKT-5001'
    customer_id TEXT NOT NULL REFERENCES customers(id),
    line_id     TEXT REFERENCES lines(id),
    subject     TEXT NOT NULL,
    description TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'resolved', 'escalated')),
    resolution  TEXT,                               -- free text written when resolved/escalated
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX idx_lines_customer ON lines(customer_id);
CREATE INDEX idx_bills_customer ON bills(customer_id);
CREATE INDEX idx_tickets_customer ON tickets(customer_id);
