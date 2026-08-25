export type SearchRole = "alert" | "comps";

export interface SearchRow {
  id: number;
  label: string;
  role: SearchRole;
  keywords: string;
  model_key: string | null;
  category_ids: string | null;
  min_price: number | null;
  max_price: number | null;
  distance_km: number | null;
  active: boolean;
}

export type ListingStatus = "active" | "reserved" | "closed";

export interface ListingRow {
  item_id: string;
  title: string | null;
  description: string | null;
  model_key: string | null;
  first_seen: string;
  last_seen: string;
  last_price: number | null;
  last_status: ListingStatus | null;
  web_url: string | null;
  image_url: string | null;
  shipping: boolean | null;

  // ------------------------------------------------ tracker scope columns
  // Every query here does `select("*")`, so these have always been present at
  // runtime; they were simply missing from the type, which is exactly why the
  // deal list spent so long ignoring them. The tracker rejects a listing on all
  // four of these *before* any margin maths (see alert_loop's candidate loop),
  // so a deal list that doesn't read them is showing listings the bot would
  // never alert on. `inAlertScope()` in constants.ts is the port.

  /** `'gpu' | 'phone'` — schema.sql. Only `gpu` may ever alert
   *  (alert_loop.ALERTING_FAMILIES); iPhones are tracked for their comps alone.
   *  Null on rows written before the column existed, which are GPUs. */
  family: string | null;
  /** True when the taxonomy lands in a laptop/prebuilt leaf. A scope rejection,
   *  not a quality one: the card inside a PC is not the trade this bot prices,
   *  because the reference it would be judged against is the loose card's. */
  whole_machine: boolean | null;
  /** `un_opened|in_box|new|as_good_as_new|good|fair|has_given_it_all`.
   *  config.BLOCKED_CONDITIONS is applied against this. */
  condition: string | null;
  /** The seller behind the listing. config.BLOCKED_SELLERS is applied against
   *  this — the only identifier that survives a relisting under a fresh id. */
  seller_id: string | null;

  // -------------------------------------------- other persisted [ext] columns
  // Not read by anything here yet, but typed so the next reader can see that
  // the tracker records them rather than re-deriving them from free text.
  brand: string | null;
  taxonomy: number[] | null;
  storage: string | null;
  country: string | null;
  location: string | null;
  distance_km: number | null;
  confidence: string | null;
  posted_at: string | null;
  modified_at: string | null;
  user_allows_shipping: boolean | null;
  ever_reserved: boolean | null;
  closed_at: string | null;
  sold_price: number | null;
}

export interface ObservationRow {
  id: number;
  item_id: string;
  price: number | null;
  status: "active" | "reserved" | null;
  seen_at: string;
}

export interface ModelPriceRow {
  model_key: string;
  ref_price: number | null;
  n_comps: number | null;
  /**
   * Comps the model owns, before borrowing from sibling SKUs via
   * models.GENERIC_FALLBACKS — `n_comps` is the total including borrowed ones.
   *
   * Optional because the column is a recent addition to the tracker's schema and
   * may not exist in the live database yet; every reader goes through
   * `ownCompCount()` in constants.ts, which falls back to `n_comps` when it is
   * absent or null.
   */
  n_own?: number | null;
  buy_ceiling: number | null;
  buy_ceiling_in_person: number | null;
  updated_at: string;
  is_seed: boolean | null;
}

export type AlertKind = "new" | "price_drop";

export interface SentAlertRow {
  id: number;
  item_id: string;
  price: number | null;
  kind: AlertKind | null;
  sent_at: string;
}

export type JunkRule = "DEFECT" | "TRADE" | "NOT_A_CARD" | "BUNDLE" | "LAPTOP" | "WANTED";

export interface JunkExclusionRow {
  id: number;
  item_id: string | null;
  title: string | null;
  rule: string | null;
  matched: string | null;
  seen_at: string;
}
