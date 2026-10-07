-- Single-owner app. Browser clients must never read this table or call these RPCs.
create table if not exists public.library_runtime (
  id integer primary key check (id = 1),
  document jsonb not null default '{}'::jsonb,
  lease_owner uuid,
  lease_until timestamptz
);
alter table public.library_runtime enable row level security;
revoke all on public.library_runtime from anon, authenticated;
grant select, update on public.library_runtime to service_role;
insert into public.library_runtime (id) values (1) on conflict do nothing;

create or replace function public.library_claim(p_owner uuid)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare changed integer;
begin
  update public.library_runtime set lease_owner = p_owner,
    lease_until = clock_timestamp() + interval '150 seconds'
  where id = 1 and (lease_until is null or lease_until < clock_timestamp());
  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

create or replace function public.library_save(p_owner uuid, p_document jsonb)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare changed integer;
begin
  update public.library_runtime set document = p_document
  where id = 1 and lease_owner = p_owner and lease_until > clock_timestamp();
  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

create or replace function public.library_unlock(p_owner uuid)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  update public.library_runtime set lease_owner = null, lease_until = null
  where id = 1 and lease_owner = p_owner;
  return found;
end;
$$;

revoke all on function public.library_claim(uuid) from public, anon, authenticated;
revoke all on function public.library_save(uuid, jsonb) from public, anon, authenticated;
revoke all on function public.library_unlock(uuid) from public, anon, authenticated;
grant execute on function public.library_claim(uuid) to service_role;
grant execute on function public.library_save(uuid, jsonb) to service_role;
grant execute on function public.library_unlock(uuid) to service_role;

-- Shared throttling survives serverless cold starts. No raw IP addresses are stored.
create table if not exists public.library_rate_limits (
  bucket text primary key,
  started_at timestamptz not null,
  attempts integer not null
);
alter table public.library_rate_limits enable row level security;
revoke all on public.library_rate_limits from anon, authenticated;
grant select, insert, update, delete on public.library_rate_limits to service_role;
create or replace function public.library_rate_limit(p_bucket text, p_limit integer, p_seconds integer)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare total integer;
begin
  if p_limit < 1 or p_seconds < 1 or p_seconds > 3600 then return false; end if;
  delete from public.library_rate_limits where started_at < clock_timestamp() - interval '1 hour';
  insert into public.library_rate_limits values (p_bucket, clock_timestamp(), 1)
  on conflict (bucket) do update set
    attempts = case when library_rate_limits.started_at < clock_timestamp() - make_interval(secs => p_seconds)
      then 1 else least(library_rate_limits.attempts + 1, 1000000) end,
    started_at = case when library_rate_limits.started_at < clock_timestamp() - make_interval(secs => p_seconds)
      then clock_timestamp() else library_rate_limits.started_at end
  returning attempts into total;
  return total <= p_limit;
end;
$$;
revoke all on function public.library_rate_limit(text, integer, integer) from public, anon, authenticated;
grant execute on function public.library_rate_limit(text, integer, integer) to service_role;
