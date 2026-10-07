-- A durable dispatch deadline prevents duplicate HTTP queues while a worker starts.
alter table public.library_accounts add column if not exists next_dispatch_at timestamptz not null default now();
create index if not exists library_accounts_dispatch_due on public.library_accounts(next_dispatch_at);

create or replace function public.library_account_save(p_account text, p_owner uuid, p_document jsonb)
returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  update public.library_accounts set document=p_document,
    next_dispatch_at=case when jsonb_typeof(p_document->'nextPollAt')='number'
      then to_timestamp((p_document->>'nextPollAt')::double precision)
      else clock_timestamp() + interval '30 seconds' end
  where account_key=p_account and lease_owner=p_owner and lease_until > clock_timestamp();
  return found;
end;
$$;
revoke all on function public.library_account_save(text,uuid,jsonb) from public, anon, authenticated;
grant execute on function public.library_account_save(text,uuid,jsonb) to service_role;
