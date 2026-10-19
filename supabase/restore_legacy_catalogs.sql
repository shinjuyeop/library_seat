-- Before rolling back to an app older than compact_account_runtime, restore its
-- read format. Keep seat_catalog and the new RPCs so either app can still run.
begin;
set local lock_timeout = '3s';
set local statement_timeout = '15s';
update public.library_accounts
set document=jsonb_set(document,'{state,seats}',seat_catalog)
where document#>'{state,seats}' is null and jsonb_typeof(document->'state')='object';
commit;
