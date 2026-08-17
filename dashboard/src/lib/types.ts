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
