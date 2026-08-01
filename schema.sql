-- Wallapop GPU deal-tracker — Supabase / Postgres schema
-- Run this once in the Supabase SQL editor.
--
-- Columns marked [ext] are extensions beyond the original spec that the
-- sale-inference and audit logic needs. Everything else is spec §4 verbatim.

-- ---------------------------------------------------------------- searches
create table if not exists searches (
  id            bigint generated always as identity primary key,
  label         text not null,
  role          text not null check (role in ('alert','comps')),
  keywords      text not null,
  model_key     text,              -- canonical model this search maps to, if specific
  category_ids  text,              -- e.g. '24200'
  min_price     numeric,
  max_price     numeric,           -- null for comps (uncapped)
  distance_km   int,
  active        boolean default true,
  unique (label)
);

-- ---------------------------------------------------------------- listings
create table if not exists listings (
  item_id       text primary key,
  title         text,
  description   text,
  model_key     text,              -- classified canonical model (nullable)
  confidence    text,              -- [ext] 'high' | 'medium' | 'low' | null
  first_seen    timestamptz default now(),
  last_seen     timestamptz default now(),
  last_price    numeric,
  last_status   text,              -- 'active' | 'reserved' | 'closed'
  web_url       text,
  image_url     text,
  shipping      boolean,
  location      text,              -- [ext] city / area string
  distance_km   numeric,           -- [ext] distance from search centre
  ever_reserved boolean default false,  -- [ext] sale-inference input
  missing_runs  int default 0,          -- [ext] consecutive comps runs unseen
  closed_at     timestamptz,            -- [ext] when inferred closed
  sold_price    numeric                 -- [ext] last price while reserved
);

create index if not exists listings_model_key_idx on listings (model_key);
create index if not exists listings_last_seen_idx on listings (last_seen);

-- ------------------------------------------------------------ observations
create table if not exists observations (
  id         bigint generated always as identity primary key,
  item_id    text references listings(item_id) on delete cascade,
  model_key  text,                 -- [ext] denormalised so comps queries are one hop
  price      numeric,
  status     text,                 -- 'active' | 'reserved'
  seen_at    timestamptz default now()
);

create index if not exists observations_model_seen_idx
  on observations (model_key, status, seen_at desc);
create index if not exists observations_item_idx on observations (item_id, seen_at desc);

-- ------------------------------------------------------------- model_prices
create table if not exists model_prices (
  model_key    text primary key,
  ref_price    numeric,            -- trimmed median of sold+reserved, 30d
  n_comps      int,
  buy_ceiling  numeric,            -- max buy price for >=50 EUR margin (shipped)
  buy_ceiling_in_person numeric,   -- [ext] same, zero-fee local pickup
  updated_at   timestamptz default now(),
  is_seed      boolean default false
);

-- -------------------------------------------------------------- sent_alerts
create table if not exists sent_alerts (
  id         bigint generated always as identity primary key,
  item_id    text,
  price      numeric,
  kind       text,                 -- 'new' | 'price_drop'
  sent_at    timestamptz default now(),
  unique (item_id, price)
);

create index if not exists sent_alerts_item_idx on sent_alerts (item_id);

-- ---------------------------------------------------- [ext] junk audit log
-- Every filtered listing, so you can tune the phrase list without guessing.
create table if not exists junk_exclusions (
  id         bigint generated always as identity primary key,
  item_id    text,
  title      text,
  phrase     text,                 -- the phrase that matched
  category   text,                 -- DEFECT | WANTED | TRADE | NOT_A_CARD
  seen_at    timestamptz default now()
);

-- ------------------------------------------------------- [ext] run history
create table if not exists run_log (
  id           bigint generated always as identity primary key,
  loop_name    text,               -- 'alert' | 'comps'
  started_at   timestamptz default now(),
  finished_at  timestamptz,
  items_seen   int default 0,
  alerts_sent  int default 0,
  errors       int default 0,
  notes        text
);
