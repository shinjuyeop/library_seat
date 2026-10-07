-- Enable pg_cron and pg_net in Supabase Dashboard > Database > Extensions first.
-- Create Vault secrets in the Dashboard (NOT in committed SQL):
-- library_app_url = https://YOUR-PRODUCTION-APP.vercel.app
-- library_cron_secret = the exact Vercel CRON_SECRET value (32+ random characters)
-- Run only after the Vercel production app is deployed.
select cron.unschedule(jobid) from cron.job where jobname = 'library-seat-poll';
select cron.schedule('library-seat-poll', '30 seconds', $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'library_app_url') || '/api/cron',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'library_cron_secret')
    ),
    body := jsonb_build_object('account', account_key),
    timeout_milliseconds := 120000
  ) from public.library_accounts
    where document ? 'credential' or document ? 'login';
$$);
