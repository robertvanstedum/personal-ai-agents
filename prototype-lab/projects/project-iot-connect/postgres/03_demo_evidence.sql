\pset pager off
\x off

\echo '=== Latest activation batch ==='
SELECT
  b.batch_number,
  b.batch_id,
  a.account_number,
  a.account_name,
  b.status,
  b.item_count,
  b.success_count,
  b.failure_count,
  b.completed_at
FROM control.activation_batches AS b
JOIN iot.accounts AS a ON a.account_id = b.account_id
ORDER BY COALESCE(b.completed_at, b.created_at) DESC
LIMIT 1;

\echo '=== Latest activation item: durable identifiers and outcomes ==='
WITH latest_item AS (
  SELECT item.*
  FROM control.activation_batch_items AS item
  ORDER BY COALESCE(item.completed_at, item.created_at) DESC
  LIMIT 1
)
SELECT
  batch.batch_number,
  item.batch_id,
  item.item_number,
  subscription.subscription_number,
  sim.iccid,
  sim.imsi,
  mdn.mdn,
  item.network_status,
  item.legacy_status,
  item.overall_status,
  item.flowone_activation_id,
  item.legacy_action_id
FROM latest_item AS item
JOIN control.activation_batches AS batch ON batch.batch_id = item.batch_id
JOIN iot.subscriptions AS subscription
  ON subscription.subscription_id = item.subscription_id
JOIN iot.sim_inventory AS sim ON sim.sim_resource_id = item.sim_resource_id
JOIN iot.mdn_inventory AS mdn ON mdn.mdn_resource_id = item.mdn_resource_id;

\echo '=== FlowOne element evidence for the latest activation item ==='
WITH latest_item AS (
  SELECT item.batch_item_id
  FROM control.activation_batch_items AS item
  ORDER BY COALESCE(item.completed_at, item.created_at) DESC
  LIMIT 1
)
SELECT
  result.sequence_number,
  result.element,
  result.operation,
  result.provisioning_status,
  result.element_code,
  result.rollback_status,
  result.applied_profile
FROM control.flowone_element_results AS result
JOIN latest_item ON latest_item.batch_item_id = result.batch_item_id
ORDER BY result.sequence_number;

\echo '=== Latest bill run for each prepared account ==='
SELECT DISTINCT ON (run.account_number)
  run.bill_run_number,
  run.bill_run_id,
  run.account_number,
  run.billing_mode,
  run.bill_cycle,
  run.status,
  run.source_charge_count,
  run.output_row_count,
  run.source_total,
  run.output_total,
  run.variance,
  run.unrepresented_source_records,
  run.duplicate_source_representations,
  run.invalid_target_lines
FROM control.bill_runs AS run
WHERE run.account_number IN ('ACCT-000100', 'ACCT-000200')
ORDER BY run.account_number, run.created_at DESC;

\echo '=== Outbound billing rows for the most recent bill run ==='
WITH latest_run AS (
  SELECT run.bill_run_id, run.bill_run_number, run.account_number
  FROM control.bill_runs AS run
  ORDER BY run.created_at DESC
  LIMIT 1
)
SELECT
  latest_run.bill_run_number,
  latest_run.account_number,
  row.row_number,
  row.posting_scope,
  row.target_line_ref,
  row.mdn,
  row.rate_plan_code,
  row.charge_code,
  row.amount,
  row.gl_code,
  row.source_record_count
FROM legacy.billing_rows AS row
JOIN latest_run ON latest_run.bill_run_id = row.bill_run_id
ORDER BY row.row_number;
