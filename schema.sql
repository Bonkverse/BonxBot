-- =========================
-- Users
-- =========================
CREATE TABLE users (
    discord_id BIGINT PRIMARY KEY,
    bonx INTEGER NOT NULL DEFAULT 100,
    rank TEXT NOT NULL DEFAULT 'citizen',
    region TEXT,
    daily_streak INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- =========================
-- Bonx Transactions (Ledger)
-- =========================
CREATE TABLE bonx_transactions (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- =========================
-- Regions (Lore)
-- =========================
CREATE TABLE regions (
    name TEXT PRIMARY KEY,
    is_capital BOOLEAN DEFAULT FALSE,
    description TEXT
);

ALTER TABLE users
    ADD COLUMN region_joined_at TIMESTAMP,
    ADD COLUMN veri_founding_resident BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE region_openings (
    region TEXT PRIMARY KEY REFERENCES regions(name),
    opened_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);

INSERT INTO regions (name, is_capital, description) VALUES
('Skintopia', FALSE, 'Creative heart of Orbana'),
('Bonktown', FALSE, 'Economic engine of the nation'),
('Creativia', FALSE, 'Artists, designers, innovators'),
('Spherehold', FALSE, 'Strategic and defensive center'),
('Versetralis', FALSE, 'Meta thinkers and architects'),
('Arena City', FALSE, 'Combat, tournaments, prestige'),
('Tulip Island', FALSE, 'Luxury escape and vacation hub'),
('Veri', TRUE, 'Capital of Bonkverse and seat of authority');

ALTER TABLE users
ADD COLUMN last_daily TIMESTAMP;

-- For databases created before the streak feature; safe to run twice.
ALTER TABLE users
ADD COLUMN IF NOT EXISTS daily_streak INTEGER NOT NULL DEFAULT 0;

ALTER TABLE users ADD COLUMN best_daily_streak INTEGER NOT NULL DEFAULT 0;

CREATE TABLE region_rank_progress (
    discord_id BIGINT NOT NULL REFERENCES users(discord_id),
    region TEXT NOT NULL REFERENCES regions(name),
    tier SMALLINT NOT NULL CHECK (tier BETWEEN 2 AND 5),
    PRIMARY KEY (discord_id, region)
);

CREATE INDEX bonx_transactions_user_id_idx ON bonx_transactions (discord_id);
