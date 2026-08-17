-- Wallapop Tracker dashboard — read-only Postgres views
--
-- APPLY THIS BY HAND, ONCE, IN THE SUPABASE SQL EDITOR.
--
-- There is no migration runner in this project: the tracker's own ../schema.sql
-- is deployed the same way (paste it into the SQL editor and run it), and this
-- file follows that convention rather than inventing a second one. Nothing in
-- the dashboard applies it automatically, and nothing here writes data — these
-- are views over tables the tracker owns.
--
-- Everything below is safe to re-run.
--
-- The dashboard degrades gracefully if this has NOT been applied: queries.ts
-- detects the missing view (undefined_table / PGRST205) and falls back to
-- computing the same numbers client-side with paged scans. The fallback is
-- exact, just chattier — so applying this is a performance fix, not a
-- correctness one.
--
-- Deliberately contains NO fee-model or margin arithmetic. The buy-ceiling maths
-- lives in exactly two places already (../config.py and src/lib/constants.ts)
-- and that duplication is a known source of drift; a third copy inside a
-- hand-applied SQL file would be the hardest of the three to notice going stale.
-- These views only join and count.

-- --------------------------------------------------- per-model_key counts
-- Replaces a ~130-request fan-out (one count + one full item_id fetch + a
-- chunked alert count per model key, for ~62 keys) with a single grouped query.
--
-- It also fixes a correctness bug rather than only a performance one. The
-- fan-out fetched every item_id for a model in one un-paged request and then
-- counted alerts against those ids, so any model with more listings than
-- PostgREST's row cap (1000 by default) silently lost the overflow and reported
-- an alert count that was too low, with nothing in the UI to say so.
--
-- `sent_alerts` has no model_key of its own — alerts are only associated with a
-- model through the listing they point at — hence the join. count(distinct
-- l.item_id) rather than count(*) because the left join fans out one listing row
-- per alert on it.
--
-- Note this is a count per *model_key*, which is not the same as a count per
-- search: listings and alerts are not tagged with the search that discovered
-- them (there is no search_id column in schema.sql), so the Searches page
-- matches on model_key and labels those figures as approximate in the UI. This
-- view makes them exactly as approximate as intended, instead of additionally
-- wrong.
create or replace view dashboard_model_key_counts as
select
  l.model_key                  as model_key,
  count(distinct l.item_id)    as listings_count,
  count(a.id)                  as alerts_count
from listings l
left join sent_alerts a on a.item_id = l.item_id
where l.model_key is not null
group by l.model_key;

comment on view dashboard_model_key_counts is
  'Dashboard: listing and alert totals per model_key. Alerts reach a model only '
  'via the listing they point at, so this join is the only way to attribute '
  'them. Read-only; see dashboard/supabase-views.sql.';
