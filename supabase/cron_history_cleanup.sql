-- Run as postgres after enabling pg_cron. Reapplying updates the same named job.
-- The production cron.timezone is GMT: 18:00 UTC is 03:00 Asia/Seoul the next day.
-- Clear all scheduler run history, including failures; account state and app events
-- live in public.library_accounts and are not part of this maintenance operation.
select cron.schedule(
  'library-seat-cron-history-cleanup',
  '0 18 * * *',
  $cleanup$
    set local lock_timeout = '3s';
    set local statement_timeout = '15s';
    truncate table only cron.job_run_details continue identity restrict;
  $cleanup$
);
