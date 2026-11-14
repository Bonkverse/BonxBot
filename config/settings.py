"""Economy tunables for BonxBot.

One place to retune the economy without hunting through command code.
These values shipped with the November 2026 economy patch; see the patch's
CHANGES.md for the reasoning behind each number.
"""

# --- Daily reward -----------------------------------------------------------
DAILY_AMOUNT = 1000        # base Bonx per claim (unchanged)
DAILY_STREAK_STEP = 100    # bonus per consecutive day
DAILY_STREAK_CAP = 6       # max bonus steps: day 7+ claims base + 6*step = 1600

# --- Slots ------------------------------------------------------------------
# Was 1.5: with the current reel weights that paid players +7.69% per spin on
# average (verified by exact probability + simulation). 1.3 puts the market
# edge at ~3.5% while keeping two-of-a-kind a "small win".
SLOT_TWO_KIND_MULTIPLIER = 1.3
SLOT_MAX_BET = 10000       # per-spin cap, bounds single-spin swings
SLOT_COOLDOWN_SECONDS = 5  # per-user cooldown between spins

# --- Community --------------------------------------------------------------
VOTE_REWARD = 100          # Bonx for voting on a skin (first vote only, not vote changes)
VOTE_CHANGE_TAX = 25       # Bonx charged when changing a vote; blocked if unaffordable
SKINTOPIA_VOTE_CHANGE_TAX = 15

# --- Misc -------------------------------------------------------------------
HISTORY_MAX_ENTRIES = 25   # standard /history limit
VERI_HISTORY_MAX_ENTRIES = 50
HISTORY_DEFAULT = 10       # default number of entries
