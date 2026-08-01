# Wallapop GPU Deal-Tracker & Flip-Margin Engine

Watches Wallapop — across Spain, Portugal, Italy and everywhere else it
operates, since it's one shared marketplace — for secondhand GPUs and pushes an
instant Telegram alert whenever a listing is a genuine flip. "Genuine" means: a
plausible negotiated offer (asking price minus a haggle discount, default 20%)
would net ≥ €50 after fees. The fair resale price is *learned* from sold +
reserved comps over the last 30 days, not from what active listings are asking.

Runs free on GitHub Actions + Supabase, unattended, GPU-only.

```
             ┌──────────────────────────────┐
             │        Supabase (PG)         │
             │ searches · listings ·        │
             │ observations · model_prices ·│
             │ sent_alerts · junk_exclusions│
             └───────────────┬──────────────┘
       ~every 5 min          │         ~every 60 min
┌──────────────────┐         │        ┌───────────────────┐
│   ALERT LOOP     │◄────────┴───────►│    COMPS LOOP     │
│ nationwide/intl  │                  │ nationwide/intl   │
│ → classify       │                  │ → record reserved │
│ → junk filter    │                  │ → infer sold      │
│ → offer margin   │                  │ → trimmed median  │
│ → Telegram       │                  │ → buy ceiling     │
└────────┬─────────┘                  └───────────────────┘
         ▼
    Telegram (sendPhoto)
```

Both loops run as a **self-dispatch chain**: each GitHub Actions run pads out
to a ~5-min (alert) / ~60-min (comps) cycle, then triggers its own next run
directly through the GitHub API. This is a workaround for a measured, real
problem — GitHub's `schedule` cron trigger fired only ~once/hour on this repo
regardless of the requested interval, while every `workflow_dispatch` call
fired instantly. The original schedule stays wired up as a low-frequency
safety net in case the chain ever breaks (crash, timeout, platform outage).

---

## Setup

### 1. Supabase

1. Create a free project at [supabase.com](https://supabase.com).
2. SQL Editor → paste all of [schema.sql](schema.sql) → Run.
3. Project Settings → API → copy the **Project URL** and the **`service_role`**
   key (server-side only — it bypasses row-level security; never ship it to a
   browser).

### 2. Telegram

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Send your new bot any message (a bot can't open a chat with you first).
3. Message [@userinfobot](https://t.me/userinfobot) to get your numeric chat id.

### 3. Local config

```bash
cp .env.example .env          # or .env.local — both are read, .env.local wins
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python seed.py      # loads the searches + day-one seed prices
```

### 4. Verify before deploying

```bash
.venv/bin/python -m pytest tests -q            # 131 tests, no network or DB needed
.venv/bin/python smoke_test.py "rtx 4070"      # hits the live API, touches no DB
DRY_RUN=1 .venv/bin/python alert_loop.py       # full loop, logs alerts instead of sending
```

`smoke_test.py` prints the raw response envelope plus every parsed listing with
its classification and junk verdict. **Run it first whenever alerts go quiet** —
it tells you immediately whether the API changed shape.

### 5. GitHub Actions

Push to a **public** repo (private repos only get 2,000 free minutes/month,
which can't sustain near-continuous polling). Then Settings → Secrets and
variables → Actions → add:

| Secret | |
|---|---|
| `TELEGRAM_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your numeric chat id |
| `SUPABASE_URL` | project URL |
| `SUPABASE_KEY` | service_role key |
| `GH_DISPATCH_TOKEN` | a token with `repo`+`workflow` scope — needed so each run can trigger its own successor (the auto `GITHUB_TOKEN` is blocked from doing this, to prevent recursive-run loops) |

Two workflows then keep themselves running via the self-dispatch chain
described above: [alert.yml](.github/workflows/alert.yml) and
[comps.yml](.github/workflows/comps.yml). Trigger either manually from the
Actions tab once to start the chain.

---

## How the margin works

```
net = ref_price·(1 − seller_fee) − [ buy·(1 + buyer_fee) + buyer_fixed + shipping_in ]

buy_ceiling = ( ref_price·(1 − seller_fee) − buyer_fixed − shipping_in − target_margin )
              ─────────────────────────────────────────────────────────────────────────
                                        1 + buyer_fee
```

| Constant | Default | |
|---|---|---|
| `SELLER_FEE` | **0** | Wallapop charges the seller nothing — only the buyer pays a protection fee on shipped sales |
| `BUYER_FEE` | 0.075 | buyer protection % (paid when *you* are the buyer) |
| `BUYER_FIXED` | 0.69 | buyer protection fixed part |
| `SHIPPING_IN` | 4.50 | inbound shipping, boxed GPU |
| `TARGET_MARGIN` | 50 | your minimum net profit |
| `OFFER_DISCOUNT` | **0.20** | how far below asking you could realistically haggle a seller down |

The gate checks a **negotiated offer**, not the raw asking price: a listing
qualifies if `asking · (1 − OFFER_DISCOUNT)` would clear `buy_ceiling`, even
when the asking price alone wouldn't — you can always propose the discount and
see if it lands. There's no hard price ceiling; a high-tier card at a high
asking price still gets shown if the haggled price pencils out. Every alert
shows the suggested offer and the net margin both at the offer and at asking.

In-person meetups (all fees zero) use `ref − target_margin` instead — both
numbers appear in every alert, though with searches now nationwide/
international, most flips will realistically be shipped.

## What gets filtered out

Configured in [junk.py](junk.py); every exclusion is written to
`junk_exclusions` with the phrase that matched, so you can audit and tune:

```sql
select category, phrase, count(*) from junk_exclusions group by 1,2 order by 3 desc;
```

- **DEFECT** — `no funciona`, `para piezas`, `no enciende`, `caja vacia`, …
- **WANTED** — titles *starting* with `busco` / `compro` (so "…no busco cambios" survives)
- **TRADE** — `cambio por`, `solo cambio`
- **NOT_A_CARD** — `waterblock`, `backplate`, `soporte grafica`, …
- **BUNDLE** — `pc gaming`, `pc gamer`, `ordenador gaming`, …
- **LAPTOP** — `legion`, `portatil`, `proart p16`/`px13`, `aorus 17`, `tuf a15`, mobile CPU suffixes, …
- **CPU** — `procesador`, `microprocesador` — catches AMD Ryzen listings whose
  model number numerically collides with a Radeon GPU's (Ryzen 5 "7600" vs
  Radeon RX "7600", both bare numbers with no differentiating suffix). This one
  is deliberately narrow: not the bare `ryzen`/`amd` brand words, since those
  can legitimately appear in a real GPU title mentioning CPU compatibility.

Only the DEFECT/WANTED/TRADE lists follow the strict phrase-only rule. BUNDLE
and LAPTOP were added after live testing showed they're the dominant problem:
a "PC Gaming … RTX 4070 Super" at €850 and a "Lenovo Legion Pro 5 rtx 4070" at
€899 both classify perfectly as a 4070 and would roughly **double** its median.
The laptop list uses bare tokens, but only laptop *product lines* that can never
appear on a card — the AIB brand words (`nitro`, `rog`, `tuf`, `strix`, `aorus`,
`pulse`, `ventus`, `hellhound`) are deliberately excluded, and there are tests
pinning that.

Both form-factor checks are skipped when a title *opens* by naming a card, so
"Gráfica RTX 4070 para PC gaming" is safe.

## Pricing intelligence

- **Comps pool** = reserved listings ∪ listings inferred sold, last 30 days, one
  price per item (a card sitting reserved for weeks contributes once, not 500×).
- **Sale inference**: reserved → absent from 2 consecutive comps runs → sold, at
  its last reserved price. Vanishing without ever being reserved closes the
  listing as *uncertain* and contributes nothing.
- **`ref_price`** = trimmed median (top/bottom 10% dropped) — kills typo prices
  and bundles. Needs `MIN_COMPS` (5) or the seed price stays in force.
- Split-VRAM SKUs (4060 Ti / 5060 Ti / 9060 XT 8 vs 16GB) get their own pools;
  a listing that omits VRAM lands in a generic key that borrows from both when
  it's short of comps.

Check on progress with:

```sql
select model_key, ref_price, n_comps, buy_ceiling, is_seed, updated_at
from model_prices order by is_seed, n_comps desc;
```

## Tuning

| Want to | Do |
|---|---|
| Change how much you'd haggle | `OFFER_DISCOUNT` in `.env` (default 0.20) |
| Ignore cheap junk/scam listings | `MIN_SANE_PRICE` (default 50 — a real GPU below that is never genuine) |
| Add a GPU model | one entry in `models.REGISTRY` (most-specific first) |
| Add/disable a search | edit the `searches` table, or `seed.py` and re-run it |
| Buy in person only | set `BUYER_FEE=0 BUYER_FIXED=0 SHIPPING_IN=0` (`SELLER_FEE` is always 0) |
| Silence a bad filter | remove the phrase/token from `junk.py` (check `junk_exclusions` first) |

## Known limits

- **GitHub Actions' `schedule` trigger is unreliable in practice, not just
  "best-effort."** Measured on this repo: over ~4.5 hours, the 5-minute alert
  cron and the 60-minute comps cron both fired only about once per hour —
  GitHub was silently throttling scheduled triggers regardless of the
  requested interval, while every manual/API `workflow_dispatch` fired
  instantly. Both workflows now self-dispatch their own next run instead of
  depending on `schedule` (see above); the cron stays wired up only as a
  low-frequency fallback. If this ever regresses, check the Actions tab for a
  broken chain (a run that didn't trigger its successor) before assuming the
  code is at fault.
- **Scheduled workflows auto-disable after 60 days of repo inactivity.** The
  comps workflow pushes a daily `.keepalive` commit to prevent that.
- **Sold data is inferred, not given** — Wallapop exposes no sold feed.
  Reserved-then-vanished is a proxy; it sharpens over 1–2 weeks. Reserved prices
  alone are already a strong signal.
- **The API drifts.** `source=search_box` is currently mandatory (without it
  every request 400s), `reserved` arrives as `{"flag": false}`, and the item list
  sits at `data.section.payload.items`. All of this is read through fallback
  chains. If fetches start failing, copy fresh headers from DevTools → Network
  into `wallapop_client.HEADERS`.
- **There's no API country filter.** Wallapop runs one shared marketplace
  across Spain, Portugal, Italy etc. rather than per-country endpoints —
  `country_code`/`country` query params are silently ignored (verified live).
  Dropping lat/lon/distance from the request is what actually surfaces
  cross-border listings, which is why both loops search nationwide now.
  Distance is still computed locally from each listing's coordinates, purely
  informational since most flips will now be shipped rather than in-person.
- Automated querying is a grey area under Wallapop's ToS. This is deliberately
  personal-scale: browser-like UA, modest polling cadence, modest pagination.

## Layout

| | |
|---|---|
| [config.py](config.py) | constants, fee models, secrets |
| [wallapop_client.py](wallapop_client.py) | search API client + defensive parsing |
| [models.py](models.py) | title → canonical `model_key` registry |
| [junk.py](junk.py) | phrase/form-factor filters |
| [pricing.py](pricing.py) | comps pool, trimmed median, margin gate |
| [alerts.py](alerts.py) | Telegram formatting + delivery |
| [db.py](db.py) | Supabase persistence |
| [alert_loop.py](alert_loop.py) · [comps_loop.py](comps_loop.py) | the two entrypoints |
| [seed.py](seed.py) | searches + seed prices |
| [smoke_test.py](smoke_test.py) | live API diagnostic, no DB |
