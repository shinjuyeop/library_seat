-- Reuse the existing private singleton; never expose its legacy account document.
set local lock_timeout = '3s';
set local statement_timeout = '15s';

revoke all on public.library_runtime from public, anon, authenticated;
grant select, update on public.library_runtime to service_role;

create or replace function public.library_access_status()
returns jsonb language sql stable security invoker set search_path = '' as $$
  select coalesce(document->'accessControl', '{}'::jsonb)
  from public.library_runtime where id=1;
$$;

create or replace function public.library_access_stop(p_reason text, p_retry_at double precision default null)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare status jsonb;
begin
  if p_reason not in ('blocked', 'rate_limited', 'unexpected_response') then
    raise exception 'Invalid access hold reason';
  end if;
  update public.library_runtime set document=jsonb_set(document, '{accessControl}',
    case when document#>>'{accessControl,blocked}'='true' then document->'accessControl'
    else jsonb_build_object('blocked',true, 'reason',p_reason,
      'blockedAt',extract(epoch from clock_timestamp()), 'retryAt',p_retry_at) end)
  where id=1 returning document->'accessControl' into status;
  if not found then raise exception 'Access control state missing'; end if;
  return status;
end;
$$;

create or replace function public.library_dispatch_due()
returns setof bigint language sql security invoker set search_path = '' as $$
  with due as (
    select account_key from public.library_accounts
    where jsonb_typeof(document->'nextPollAt')='number'
      and (document ? 'credential' or document ? 'login' or document->>'pendingWork'='true')
      and next_dispatch_at <= clock_timestamp()
      and (lease_until is null or lease_until < clock_timestamp())
      and (not coalesce((select document#>>'{accessControl,blocked}'='true'
                         from public.library_runtime where id=1), false)
           or document#>'{state,notifications}' @> '[{"delivery":"pending"}]'::jsonb)
    order by next_dispatch_at
    for update skip locked
  ), dispatched as (
    update public.library_accounts a
    set next_dispatch_at=clock_timestamp() + interval '150 seconds'
    from due where a.account_key=due.account_key returning a.account_key
  )
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name='library_app_url') || '/api/cron',
    headers := jsonb_build_object('Content-Type','application/json',
      'Authorization','Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name='library_cron_secret')),
    body := jsonb_build_object('account',account_key), timeout_milliseconds := 120000
  ) from dispatched;
$$;

revoke all on function public.library_access_status() from public, anon, authenticated;
revoke all on function public.library_access_stop(text,double precision) from public, anon, authenticated;
revoke all on function public.library_dispatch_due() from public, anon, authenticated;
grant execute on function public.library_access_status() to service_role;
grant execute on function public.library_access_stop(text,double precision) to service_role;
grant execute on function public.library_dispatch_due() to service_role;

-- Updating this rollout must never re-enable the user's paused scheduler.
select cron.alter_job(job_id := jobid, command := 'select public.library_dispatch_due();', active := false)
from cron.job where jobname='library-seat-poll';
notify pgrst, 'reload schema';
