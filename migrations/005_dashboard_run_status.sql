-- Dashboard "last update" / "refresh in progress" indicator: let the
-- read-only dashboard role see the ingestion run log (metadata only —
-- timestamps, row counts, status; no activity content, no personal data).
--
-- PURELY ADDITIVE: one SELECT grant + its RLS policy. Drop-to-revert:
--   drop policy dashboard_reader_select on ingestion_runs;
--   revoke select on ingestion_runs from dashboard_reader;
--
-- Why: the dashboard shows "Last update: <when>" and an active
-- "data is being refreshed" banner instead of a hardcoded caption —
-- both must come from the real run log (PM requirement, 2026-07-20).

grant select on ingestion_runs to dashboard_reader;

do $$
begin
    if not exists (select 1 from pg_policies where schemaname = 'public'
                   and tablename = 'ingestion_runs' and policyname = 'dashboard_reader_select') then
        create policy dashboard_reader_select on ingestion_runs
            for select to dashboard_reader using (true);
    end if;
end $$;
