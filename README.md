# Wallapop GPU Deal-Tracker & Flip-Margin Engine

Watches Wallapop for secondhand GPUs and pushes an instant Telegram alert **only
when a listing is a genuine flip** — cheap enough to resell for ≥ €50 net after
fees. The fair resale price is *learned* from sold + reserved comps over the last
30 days, not from what active listings are asking.

Runs free on GitHub Actions + Supabase, unattended.

```
             ┌──────────────────────────────┐
             │        Supabase (PG)         │
             │ searches · listings ·        │
             │ observations · model_prices ·│
             │ sent_alerts · junk_exclusions│
             └───────────────┬──────────────┘
       every 5 min           │          every 60 min
┌──────────────────┐         │        ┌───────────────────┐
│   ALERT LOOP     │◄────────┴───────►│    COMPS LOOP     │
│ capped searches  │                  │ uncapped searches │
│ → classify       │                  │ → record reserved │
│ → junk filter    │                  │ → infer sold      │
│ → margin gate    │                  │ → trimmed median  │
│ → Telegram       │                  │ → buy ceiling     │
└────────┬─────────┘                  └───────────────────┘
         ▼
    Telegram (sendPhoto)
```

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
.venv/bin/python -m pytest tests -q     # 102 tests, no network or DB needed
.venv/bin/python smoke_test.py "rtx 4070"   # hits the live API, touches no DB
DRY_RUN=1 .venv/bin/python alert_loop.py    # full loop, logs alerts instead of sending
```

`smoke_test.py` prints the raw response envelope plus every parsed listing with
its classification and junk verdict. **Run it first whenever alerts go quiet** —
it tells you immediately whether the API changed shape.

### 5. GitHub Actions

Push to a **public** repo (private repos only get 2,000 free minutes/month,
which can't sustain 5-minute polling). Then Settings → Secrets and variables →
Actions → add:

| Secret | |
|---|---|
| `TELEGRAM_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your numeric chat id |
| `SUPABASE_URL` | project URL |
| `SUPABASE_KEY` | service_role key |

Two workflows then run themselves: [alert.yml](.github/workflows/alert.yml)
(`*/5 * * * *`) and [comps.yml](.github/workflows/comps.yml) (`0 * * * *`).
Trigger either manually from the Actions tab to confirm.

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
| `SELLER_FEE` | 0.10 | Wallapop selling fee when shipped |
| `BUYER_FEE` | 0.075 | buyer protection % |
| `BUYER_FIXED` | 0.69 | buyer protection fixed part |
| `SHIPPING_IN` | 4.50 | inbound shipping, boxed GPU |
| `TARGET_MARGIN` | 50 | your minimum net profit |
| `MAX_DEAL_PRICE` | **350** | hard budget ceiling — never alert above this |

A 4070 with a €330 reference must be **≤ €224 shipped** to clear €50 — which is
why a flat "4070 under €400" rule alerts on junk. The in-person Madrid preset
(all fees zero) gives `ref − 50 = €280`, and both numbers appear in every alert.

Every alert is additionally hard-capped at `MAX_DEAL_PRICE` (€350): a 4090 at
€700 may be a superb margin, but it's not a deal you'll be shown.

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
- **LAPTOP** — `legion`, `portatil`, `proart`, `aorus 17`, `tuf a15`, mobile CPU suffixes, …

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
| Change the budget ceiling | `MAX_DEAL_PRICE` in `.env` / repo secrets |
| Add a GPU model | one entry in `models.REGISTRY` (most-specific first) |
| Add/disable a search | edit the `searches` table, or `seed.py` and re-run it |
| Buy in person only | set `SELLER_FEE=0 BUYER_FEE=0 BUYER_FIXED=0 SHIPPING_IN=0` |
| Widen/narrow the area | `WP_DEFAULT_DISTANCE_KM`, or `distance_km` per search row |
| Silence a bad filter | remove the phrase from `junk.py` (check `junk_exclusions` first) |

## Known limits

- **GitHub Actions cron is best-effort.** 5-minute floor, UTC only, and delays
  of 10–30 min under load (worst at the top of the hour). For tighter polling,
  run `alert_loop.run_once()` in a `while` loop on an Oracle Cloud Always Free
  VM — both loops are already written as host-agnostic `run_once()` entrypoints,
  so it's a config change, not a rewrite.
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
- **Listings aren't distance-tagged by the API** — distance is computed locally
  from each listing's coordinates against your configured centre.
- Automated querying is a grey area under Wallapop's ToS. This is deliberately
  personal-scale: browser-like UA, 5-minute polling, modest pagination.

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
