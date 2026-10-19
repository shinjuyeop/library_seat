-- Synthetic rows only; the entire test rolls back. Never calls the library API.
begin;
set local lock_timeout = '3s';
set local statement_timeout = '15s';
set local role service_role;
do $test$
declare
  account_a text := repeat('0',63) || '1';
  account_b text := repeat('0',63) || '2';
  owner_a uuid := '00000000-0000-0000-0000-000000000001';
  owner_b uuid := '00000000-0000-0000-0000-000000000002';
  catalog jsonb := '[{"key":"102:3","roomId":102,"number":"3","id":102003}]';
  replacement jsonb := '[{"key":"232:1","roomId":232,"number":"1","id":232001}]';
  state jsonb := jsonb_build_object('state',jsonb_build_object('running',false,'events','[]'::jsonb),
                                    'nextPollAt',extract(epoch from clock_timestamp())+3600);
  acquired jsonb;
begin
  if exists(select 1 from public.library_accounts where account_key in (account_a,account_b)) then
    raise exception 'Synthetic account collision';
  end if;
  insert into public.library_accounts(account_key,document,seat_catalog,next_dispatch_at)
  values(account_a,state,catalog,clock_timestamp()+interval '1 hour'),
        (account_b,state,replacement,clock_timestamp()+interval '1 hour');

  acquired := public.library_account_acquire(account_a,owner_a,false);
  if acquired is null or acquired->'state' ? 'seats' then
    raise exception 'Compact acquire must omit the catalog';
  end if;
  if public.library_account_acquire(account_a,owner_b,true) is not null then
    raise exception 'A second worker must not acquire an active lease';
  end if;
  if public.library_account_commit(account_a,owner_b,state,replacement,true) then
    raise exception 'Wrong-owner commit must fail';
  end if;
  if not public.library_account_commit(account_a,owner_a,state,null,false) then
    raise exception 'Intermediate commit failed';
  end if;
  if (select lease_owner from public.library_accounts where account_key=account_a) is distinct from owner_a
    or public.library_account_read(account_a)#>'{state,seats}' is distinct from catalog
    or public.library_account_read(account_b)#>'{state,seats}' is distinct from replacement then
    raise exception 'Intermediate commit changed lease, omitted catalog, or another account';
  end if;

  update public.library_accounts set lease_until=clock_timestamp()-interval '1 second'
  where account_key=account_a;
  if public.library_account_commit(account_a,owner_a,state,replacement,true) then
    raise exception 'Expired lease must not write state or catalog';
  end if;
  acquired := public.library_account_acquire(account_a,owner_b,true);
  if acquired#>'{state,seats}' is distinct from catalog then
    raise exception 'A new owner must receive the full catalog';
  end if;
  perform public.library_account_unlock(account_a,owner_a);
  if (select lease_owner from public.library_accounts where account_key=account_a) is distinct from owner_b then
    raise exception 'Stale unlock released a newer owner';
  end if;
  if not public.library_account_commit(account_a,owner_b,state,replacement,true) then
    raise exception 'Final commit failed';
  end if;
  if (select lease_owner from public.library_accounts where account_key=account_a) is not null
    or public.library_account_read(account_a)#>'{state,seats}' is distinct from replacement
    or (select document#>'{state,seats}' from public.library_accounts where account_key=account_a) is not null then
    raise exception 'Final commit must save the catalog separately and release atomically';
  end if;
  if (select next_dispatch_at from public.library_accounts where account_key=account_a)
      is distinct from to_timestamp((state->>'nextPollAt')::double precision) then
    raise exception 'Final commit did not preserve the requested dispatch deadline';
  end if;

  update public.library_accounts set document=jsonb_set(document,'{state,running}','true')
  where account_key=account_a;
  acquired := public.library_account_acquire(account_a,owner_a,false);
  if acquired#>'{state,seats}' is distinct from replacement then
    raise exception 'An active wait must receive its cached catalog';
  end if;
  if not public.library_account_commit(account_a,owner_a,state,'[]'::jsonb,true) then
    raise exception 'Empty catalog commit failed';
  end if;
  if public.library_account_read(account_a)#>'{state,seats}' is distinct from '[]'::jsonb then
    raise exception 'Explicit empty catalog must clear the cache';
  end if;

  -- A rolling deployment must also see updates saved by the previous app.
  update public.library_accounts set document=jsonb_set(state,'{state,seats}',catalog)
  where account_key=account_a;
  acquired := public.library_account_acquire(account_a,owner_a,false);
  if acquired->'state' ? 'seats' then raise exception 'Legacy acquire was not compact'; end if;
  if not public.library_account_commit(account_a,owner_a,state,replacement,true) then
    raise exception 'Legacy catalog commit failed';
  end if;
  if (select document#>'{state,seats}' from public.library_accounts where account_key=account_a) is distinct from replacement then
    raise exception 'Rolling compatibility did not preserve the legacy catalog';
  end if;

  if exists(select 1 from unnest(array[
      'public.library_account_read(text)',
      'public.library_account_acquire(text,uuid,boolean)',
      'public.library_account_commit(text,uuid,jsonb,jsonb,boolean)']) as f(signature)
      cross join unnest(array['anon','authenticated']) as r(role_name)
      where has_function_privilege(r.role_name,f.signature,'execute'))
    or not (select relrowsecurity from pg_class where oid='public.library_accounts'::regclass) then
    raise exception 'Runtime RPCs or account data are exposed to browser roles';
  end if;
end;
$test$;
select 'compact runtime SQL checks passed; synthetic rows rolled back' as result;
rollback;
