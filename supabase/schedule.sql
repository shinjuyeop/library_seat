-- Enable pg_cron and pg_net in Supabase Dashboard > Database > Extensions first.
-- Create Vault secrets in the Dashboard (NOT in committed SQL):
-- library_app_url = https://YOUR-PRODUCTION-APP.vercel.app
-- library_cron_secret = the exact Vercel CRON_SECRET value (32+ random characters)
-- Apply migration 202610070003_poll_dispatch.sql and deploy the app first.
select cron.unschedule(jobid) from cron.job where jobname = 'library-seat-poll';
select cron.schedule('library-seat-poll', '1 second', $$
  with due as (
    select account_key from public.library_accounts
    where (document ? 'credential' or document ? 'login')
      and next_dispatch_at <= clock_timestamp()
      and (lease_until is null or lease_until < clock_timestamp())
    order by next_dispatch_at
    for update skip locked
  ), dispatched as (
    update public.library_accounts a
    set next_dispatch_at=clock_timestamp() + interval '150 seconds'
    from due where a.account_key=due.account_key
    returning a.account_key
  )
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'library_app_url') || '/api/cron',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'library_cron_secret')
    ),
    body := jsonb_build_object('account', account_key),
    timeout_milliseconds := 120000
  ) from dispatched;
$$);
