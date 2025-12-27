-- =========================
-- Users
-- =========================
CREATE TABLE users (
    discord_id BIGINT PRIMARY KEY,
    bonx INTEGER NOT NULL DEFAULT 100,
    rank TEXT NOT NULL DEFAULT 'citizen',
    region TEXT,
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

INSERT INTO regions (name, is_capital, description) VALUES
('Skintopia', FALSE, 'Creative heart of Orbana'),
('Bonktown', FALSE, 'Economic engine of the nation'),
('Creativia', FALSE, 'Artists, designers, innovators'),
('Spherehold', FALSE, 'Strategic and defensive center'),
('Versetralis', FALSE, 'Meta thinkers and architects'),
('Arena City', FALSE, 'Combat, tournaments, prestige'),
('Tulip Island', FALSE, 'Luxury escape and vacation hub'),
('Veri', TRUE, 'Capital of Bonkverse and seat of authority');
