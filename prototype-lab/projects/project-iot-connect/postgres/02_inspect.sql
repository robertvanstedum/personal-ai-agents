-- Reusable IoT Connect PostgreSQL inspection queries.

SELECT current_user, current_database(), current_schema();

SELECT
  schemaname,
  tablename
FROM pg_catalog.pg_tables
WHERE schemaname IN ('iot', 'legacy', 'control', 'catalog')
ORDER BY schemaname, tablename;

SELECT
  account_number,
  account_name,
  external_billing_account_number,
  send_subscriptions_to_amdocs,
  billing_mode,
  status
FROM iot.accounts
ORDER BY account_number;

SELECT
  current_owner_type,
  resource_status,
  count(*) AS sim_count
FROM iot.sim_inventory
GROUP BY current_owner_type, resource_status
ORDER BY current_owner_type, resource_status;

SELECT
  status,
  count(*) AS mdn_count
FROM iot.mdn_inventory
GROUP BY status
ORDER BY status;

SELECT
  batch_number,
  account_id,
  status,
  item_count,
  success_count,
  failure_count,
  created_at
FROM control.activation_batches
ORDER BY created_at DESC;

SELECT
  bill_run_number,
  account_number,
  billing_mode,
  bill_cycle,
  source_charge_count,
  output_row_count,
  source_total,
  output_total,
  variance
FROM control.bill_runs
ORDER BY created_at DESC;
