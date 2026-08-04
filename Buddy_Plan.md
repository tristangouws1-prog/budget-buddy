# The Buddy — Implementation Plan

**Goal (from the `#future` block in To_Do_List.txt):** a pixel companion living on the
left side of the screen that reacts to your budget health, earns XP from good money
habits, hatches from eggs, and can be dressed up and housed.

---

## Ground rules (read first, every session)

- **Mixed split:** the user writes route logic and models; Claude writes migrations,
  boilerplate, templates, CSS/sprites, JS, and tests. Present each step's code plan
  in chat before the user types their part. Never write app.py logic unprompted.
- **Never read `.env`** — it holds the user's keys.
- The app lives at the **repo root**; the server is PythonAnywhere free tier
  (see memory file `budget-buddy-deployment`). After any DB change on the server,
  **Reload the web app** or SQLite throws `disk I/O error`.
- New tables are created with `db.create_all()` (it never adds columns to existing
  tables). On the server run it by hand — it only runs under `__main__`:
  `python3 -c "from app import app, db; app.app_context().push(); db.create_all()"`
- Verification scripts go in the scratchpad and must use the isolated-engine pattern
  (`del app.extensions["sqlalchemy"]; db.init_app(app)`) so tests never touch the
  real `budget.db`.
- Keep `To_Do_List.txt` in sync with any TODO edits made in `app.py`.

---

## Status tracker

| Phase | State |
|-------|-------|
| B1 — buddy appears | **Code complete** (2026-07-31, user approved Claude writing both halves). `Buddy` model, `BUDDY_MESSAGES`, `buddy_mood()`, `inject_buddy()` context processor in app.py; `templates/_buddy.html`; buddy CSS in `static/style.css`; include in `base.html`. End-to-end tests pass on an isolated DB. **Remaining:** user runs `python app.py` once locally (creates the `buddy` table), visual check, then commit/push + server deploy (git pull, create_all, Reload). |
| B2 — XP + levels | Not started |
| B3 — eggs | Not started |
| B4 — cosmetics | Not started |
| B5 — house | Not started |

---

## Phase B1 — The buddy appears (static companion)

**Data (user writes):** a `Buddy` model — `id`, `user_id` (FK, `unique=True`, one per
user for now), `name` (default "Buddy"), `species` (string key, only "blobcat" exists),
`stage` ("egg"/"hatched" — default "hatched" until B3), `xp` (default 0), `created_at`.
Created **lazily in the context processor** when missing, which covers both new
registrations and existing users with no extra hook.

**Mood logic (user writes):** `buddy_mood(user)` returning `(mood, message)` where
mood is `happy` / `neutral` / `worried`:
- any bill with `get_status(p) == "overdue"` → `worried` (message names the bill)
- bills exist and all `is_paid` → `happy`
- otherwise → `neutral`
Messages come from a `BUDDY_MESSAGES` dict (per-mood lists, `random.choice`).
Needs `import random`.

**Context processor (user writes):** `inject_buddy()` supplying `buddy`, `buddy_mood`,
`buddy_message` to every template. Returns `{}` for anonymous users.

**Display (Claude — done):** `templates/_buddy.html`, included from `base.html`.
The partial guards itself (`buddy is defined and buddy`) so the site works before
the app.py half exists. Fixed dock bottom-left: sprite + speech bubble + name pill.
Mobile (≤720px): bubble and name hidden, tap the sprite to toggle the bubble.
Desktop: clicking the sprite dismisses/restores the bubble (same class, two
media-scoped rules).

**Sprite (Claude — done):** inline SVG pixel art (`<rect>` grid, `crispEdges`),
16×16 viewBox — a purple blob-cat. Colours come from CSS variables scoped to
`.buddy-dock` (`--buddy-body`, `--buddy-ink`, `--buddy-cheek`) with a
`body.theme-dark` override. Moods swap eye/mouth/brow `<g>` groups purely via the
`buddy-mood-*` class on the dock — no JS. Idle bob + blink are CSS keyframes.

**After the user's half:** run the app once locally (`python app.py` triggers
`db.create_all()` under `__main__`), verify visually in all moods, run the
verification script, then commit/push, `git pull` + create_all + **Reload** on
PythonAnywhere.

---

## Phase B2 — XP and levels

**Data (user writes):** `XpEvent` — `id`, `user_id`, `kind`
("register_bill"/"pay_bill"/"check_in"/"clear_carryover"), `ref_key` (string),
`amount`, `created_at`. **Unique index on (user_id, kind, ref_key)** — SQLite can't
add UNIQUE via ALTER but `CREATE UNIQUE INDEX` post-hoc is fine. This makes every
award idempotent: marking a bill unpaid/paid repeatedly can't grind XP.

**Helper (user writes, Claude specs):** `award_xp(user, kind, ref_key, amount)` —
insert the event if new, add to `buddy.xp`, return whether it awarded. Values:
- add a bill: **+10**, `ref_key="bill:<payment_id>"`
- pay a bill: **+15**, keyed per month `"pay:<id>:<YYYY-MM>"` (weekly bills per ISO week)
- daily check-in: **+5**, awarded from the context processor, keyed `"day:<YYYY-MM-DD>"`
- clearing carried-over debt: **+20**

**Hooks:** `add_payment`, `mark_paid`, `partial_pay` (only when it completes the
bill), `clear_carryover`, and the context processor (check-in).

**Level (derived, no column):** `level = int((xp / 50) ** 0.5) + 1`
(level 2 at 50 XP, 3 at 200, 4 at 450). Claude adds "Lv N" + thin XP progress bar
under the sprite in `_buddy.html`. Flash message on level-up.

**Tests (Claude):** double-award attempts, pay/unpay/pay cycle, one check-in per
day, level formula boundaries.

## Phase B3 — Eggs and hatching

- New buddies arrive as eggs: `stage="egg"` + `hatch_xp` threshold (~100 XP earned
  while the egg is active). New registrations start with an egg; existing hatched
  buddies untouched.
- Egg sprite: same SVG technique, crack widens at 25/50/75% progress. Hatch =
  one-time CSS animation + flash; species picked at hatch (random from 2–3
  recolours before drawing new shapes).
- Later eggs at level milestones; add `is_active` to `Buddy` (ALTER TABLE ADD
  COLUMN is fine) so the user picks who's on screen (Settings).

## Phase B4 — Cosmetics

- **Currency:** coins earned alongside XP 1:1 — XP is lifetime (levels), coins are
  spendable. One new column `buddy.coins` via ALTER TABLE.
- **Catalog:** Python dict in app.py (key, name, price, slot `hat`/`accessory`,
  min level) — no DB table for static data. Owned items in `OwnedCosmetic`
  (`user_id`, `item_key`, `equipped` bool), unique on (user_id, item_key).
- **Shop UI (Claude):** a `/buddy` page — portrait, XP bar, shop grid,
  equip/unequip. Cosmetics render as extra SVG layers over the sprite.

## Phase B5 — The house

- The `/buddy` page grows a scene: an SVG room the buddy stands in. Decorations
  are cosmetics with slots `wall`/`floor`/`furniture` bought with coins — reuses
  the whole B4 system, so this phase is mostly art and CSS.

---

## Decisions already made with the user

1. Buddy appears on **every page** (context processor), not just the dashboard.
2. Cosmetics use **coins**, not spent XP.
3. First species: **blobcat** (purple, matches the 💜 logo).
4. Buddy naming: small form on the `/buddy` page (Phase B4), default "Buddy".
