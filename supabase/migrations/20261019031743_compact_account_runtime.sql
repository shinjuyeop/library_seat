-- Expand first: old workers can continue using document.state.seats during rollout.
-- Once all old workers have drained, run supabase/compact_account_catalogs.sql.
set local lock_timeout = '3s';
set local statement_timeout = '15s';

alter table public.library_accounts
  add column if not exists seat_catalog jsonb not null default '[]'::jsonb;

create or replace function public.library_account_read(p_account text)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select a.document || jsonb_build_object('state',
    coalesce(a.document->'state', '{}'::jsonb) || jsonb_build_object('seats',
      coalesce(a.document#>'{state,seats}', a.seat_catalog)))
  from public.library_accounts a where a.account_key=p_account;
$$;

create or replace function public.library_account_acquire(
  p_account text, p_owner uuid, p_catalog boolean default true)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare claimed jsonb;
begin
  update public.library_accounts set lease_owner=p_owner,
    lease_until=clock_timestamp() + interval '150 seconds'
  where account_key=p_account and (lease_until is null or lease_until < clock_timestamp())
  returning document into claimed;
  if not found then return null; end if;
  -- Active waits need their cached seats for fast polling and partial-room merges.
  if p_catalog or claimed#>>'{state,running}'='true' then
    return public.library_account_read(p_account);
  end if;
  return claimed #- '{state,seats}';
end;
$$;

create or replace function public.library_account_commit(
  p_account text, p_owner uuid, p_document jsonb,
  p_catalog jsonb default null, p_release boolean default false)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  if jsonb_typeof(p_document) is distinct from 'object'
    or jsonb_typeof(p_document->'state') is distinct from 'object'
    or (p_catalog is not null and jsonb_typeof(p_catalog)<>'array') then
    raise exception 'Invalid account state or seat catalog';
  end if;
  update public.library_accounts a set
    document=case when a.document#>'{state,seats}' is not null then
      -- Preserve compatibility until the old deployment's workers have drained.
      jsonb_set(p_document #- '{state,seats}', '{state,seats}',
        coalesce(p_catalog, a.document#>'{state,seats}', a.seat_catalog))
      else p_document #- '{state,seats}' end,
    seat_catalog=coalesce(p_catalog, a.seat_catalog),
    next_dispatch_at=case when jsonb_typeof(p_document->'nextPollAt')='number'
      then to_timestamp((p_document->>'nextPollAt')::double precision)
      else clock_timestamp() + interval '30 seconds' end,
    lease_owner=case when p_release then null else a.lease_owner end,
    lease_until=case when p_release then null else a.lease_until end
  where a.account_key=p_account and a.lease_owner=p_owner and a.lease_until > clock_timestamp();
  return found;
end;
$$;

revoke all on function public.library_account_read(text) from public, anon, authenticated;
revoke all on function public.library_account_acquire(text,uuid,boolean) from public, anon, authenticated;
revoke all on function public.library_account_commit(text,uuid,jsonb,jsonb,boolean) from public, anon, authenticated;
grant execute on function public.library_account_read(text) to service_role;
grant execute on function public.library_account_acquire(text,uuid,boolean) to service_role;
grant execute on function public.library_account_commit(text,uuid,jsonb,jsonb,boolean) to service_role;

notify pgrst, 'reload schema';
