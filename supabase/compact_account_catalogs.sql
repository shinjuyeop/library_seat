-- Run only after the compact-runtime app is live and old workers have drained
-- (wait at least 150 seconds after the production switch). Safe to rerun.
-- Account rows retain their reservation state, credentials, events and leases.
begin;
set local lock_timeout = '3s';
set local statement_timeout = '15s';
update public.library_accounts
set seat_catalog=document#>'{state,seats}', document=document #- '{state,seats}'
where jsonb_typeof(document#>'{state,seats}')='array';
commit;
