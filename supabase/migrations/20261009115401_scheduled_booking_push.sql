-- Only the Flask server accesses these encrypted, account-scoped subscriptions.
create table public.library_push_devices (
  id text primary key check (id ~ '^[a-f0-9]{64}$'),
  account_key text not null references public.library_accounts(account_key) on delete cascade,
  subscription text not null,
  preferences jsonb not null,
  updated_at timestamptz not null default now()
);
create index library_push_devices_account on public.library_push_devices(account_key);
alter table public.library_push_devices enable row level security;
revoke all on public.library_push_devices from public, anon, authenticated;
grant select, insert, update, delete on public.library_push_devices to service_role;

create function public.library_push_put(p_account text, p_id text, p_subscription text, p_preferences jsonb)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  perform 1 from public.library_accounts where account_key=p_account for update;
  if not found then return false; end if;
  if (select count(*) from public.library_push_devices where account_key=p_account and id<>p_id) >= 3 then
    return false;
  end if;
  insert into public.library_push_devices(id, account_key, subscription, preferences)
    values (p_id, p_account, p_subscription, p_preferences)
    on conflict (id) do update set account_key=excluded.account_key, subscription=excluded.subscription,
      preferences=excluded.preferences, updated_at=now();
  return true;
end;
$$;
create function public.library_push_delete(p_account text, p_id text)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  delete from public.library_push_devices where account_key=p_account and id=p_id;
  return true;
end;
$$;
revoke all on function public.library_push_put(text,text,text,jsonb) from public, anon, authenticated;
revoke all on function public.library_push_delete(text,text) from public, anon, authenticated;
grant execute on function public.library_push_put(text,text,text,jsonb) to service_role;
grant execute on function public.library_push_delete(text,text) to service_role;

-- Preserve the existing destination and secret without copying either into a migration.
-- A disconnected account still needs its scheduled failure and pending push delivered.
do $$
declare existing record;
begin
  for existing in select jobid, command from cron.job where jobname='library-seat-poll' loop
    if position('pendingWork' in existing.command)=0 then
      if position('document ? ''credential''' in existing.command)=0 then
        raise exception 'Scheduler predicate changed; review before updating';
      end if;
      perform cron.alter_job(existing.jobid, command := replace(existing.command,
        'document ? ''credential''', '(document ? ''credential'' OR document->>''pendingWork'' = ''true'')'));
    end if;
  end loop;
end;
$$;
