-- Account keys are HMACs of the provider's stable patron ID. No browser access.
create table if not exists public.library_accounts (
  account_key text primary key check (account_key ~ '^[a-f0-9]{64}$'),
  document jsonb not null default '{}'::jsonb,
  lease_owner uuid,
  lease_until timestamptz
);
alter table public.library_accounts enable row level security;
revoke all on public.library_accounts from public, anon, authenticated;
grant select, insert, update on public.library_accounts to service_role;

create or replace function public.library_account_ensure(p_account text)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  insert into public.library_accounts(account_key) values(p_account) on conflict do nothing;
  return true;
end;
$$;
create or replace function public.library_account_claim(p_account text, p_owner uuid)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  update public.library_accounts set lease_owner=p_owner,
    lease_until=clock_timestamp() + interval '150 seconds'
  where account_key=p_account and (lease_until is null or lease_until < clock_timestamp());
  return found;
end;
$$;
create or replace function public.library_account_save(p_account text, p_owner uuid, p_document jsonb)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  update public.library_accounts set document=p_document
  where account_key=p_account and lease_owner=p_owner and lease_until > clock_timestamp();
  return found;
end;
$$;
create or replace function public.library_account_unlock(p_account text, p_owner uuid)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  update public.library_accounts set lease_owner=null, lease_until=null
  where account_key=p_account and lease_owner=p_owner;
  return found;
end;
$$;
revoke all on function public.library_account_ensure(text) from public, anon, authenticated;
revoke all on function public.library_account_claim(text,uuid) from public, anon, authenticated;
revoke all on function public.library_account_save(text,uuid,jsonb) from public, anon, authenticated;
revoke all on function public.library_account_unlock(text,uuid) from public, anon, authenticated;
grant execute on function public.library_account_ensure(text) to service_role;
grant execute on function public.library_account_claim(text,uuid) to service_role;
grant execute on function public.library_account_save(text,uuid,jsonb) to service_role;
grant execute on function public.library_account_unlock(text,uuid) to service_role;
