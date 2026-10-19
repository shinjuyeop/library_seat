-- Enable pg_cron and pg_net in Supabase Dashboard > Database > Extensions first.
-- Create Vault secrets in the Dashboard (NOT in committed SQL):
-- library_app_url = https://YOUR-PRODUCTION-APP.vercel.app
-- library_cron_secret = the exact Vercel CRON_SECRET value (32+ random characters)
-- Apply migration 20261019083459_library_access_policy.sql and deploy the app first.
select cron.unschedule(jobid) from cron.job where jobname = 'library-seat-poll';
select cron.schedule('library-seat-poll', '1 second', $$
  select public.library_dispatch_due();
$$);
