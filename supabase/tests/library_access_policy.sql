-- pg_net sends only after commit. Rollback removes every synthetic queued request.
begin;
set local lock_timeout='3s';
set local statement_timeout='15s';
do $$
declare original jsonb; held jsonb; queued bigint; before_queue bigint;
begin
  if has_function_privilege('anon','public.library_access_status()','EXECUTE')
    or has_function_privilege('authenticated','public.library_access_stop(text,double precision)','EXECUTE')
    or has_function_privilege('anon','public.library_dispatch_due()','EXECUTE') then
    raise exception 'Client roles can access private control RPCs';
  end if;
  if not has_function_privilege('service_role','public.library_access_status()','EXECUTE') then
    raise exception 'Server cannot inspect access control';
  end if;
  select document-'accessControl' into original from public.library_runtime where id=1;
  update public.library_runtime set document=jsonb_set(document,'{accessControl}','{}') where id=1;
  held := public.library_access_stop('rate_limited', extract(epoch from clock_timestamp())+3600);
  perform public.library_access_stop('blocked');
  if public.library_access_status() is distinct from held then
    raise exception 'A later error replaced the original hold or retry deadline';
  end if;
  if (select document-'accessControl' from public.library_runtime where id=1) is distinct from original then
    raise exception 'Holding access changed legacy private data';
  end if;
  if exists(select 1 from public.library_accounts where account_key=repeat('f',64)) then
    raise exception 'Synthetic account collides with real data';
  end if;
  -- Isolate real accounts inside this transaction; none of these changes commit.
  update public.library_accounts set next_dispatch_at=clock_timestamp()+interval '1 day';
  insert into public.library_accounts(account_key,document,next_dispatch_at)
  values(repeat('f',64),'{"credential":"synthetic","state":{"running":false},"nextPollAt":null}',clock_timestamp());
  update public.library_runtime set document=jsonb_set(document,'{accessControl}','{"blocked":false}') where id=1;
  select count(*) into queued from public.library_dispatch_due();
  if queued<>0 then raise exception 'Idle account was dispatched'; end if;
  select count(*) into before_queue from net.http_request_queue;
  update public.library_accounts set document=jsonb_set(document,'{nextPollAt}',to_jsonb(extract(epoch from clock_timestamp())))
  where account_key=repeat('f',64);
  select count(*) into queued from public.library_dispatch_due();
  if queued<>1 or (select count(*) from net.http_request_queue)<>before_queue+1 then
    raise exception 'Due account was not queued exactly once';
  end if;
  select count(*) into queued from public.library_dispatch_due();
  if queued<>0 then raise exception 'Dispatch deadline allowed a duplicate'; end if;
  perform public.library_access_stop('blocked');
  update public.library_accounts set next_dispatch_at=clock_timestamp() where account_key=repeat('f',64);
  select count(*) into queued from public.library_dispatch_due();
  if queued<>0 then raise exception 'Global access hold allowed a provider job'; end if;
end;
$$;
rollback;
select 'passed; synthetic data and HTTP queue rolled back' as result;
