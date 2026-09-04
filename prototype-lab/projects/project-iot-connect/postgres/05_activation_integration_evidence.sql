-- IoT Connect activation integration evidence
-- One row per FlowOne network-element operation for the customer's latest batch.
-- Amdocs/Legacy Billing submission evidence is repeated beside each SIM so the
-- complete post-activation decision is visible in one TablePlus result.

WITH target_account AS (
  SELECT account_id, account_number, account_name
  FROM iot.accounts
  WHERE account_number = 'ACCT-000200' -- change to ACCT-000100 for Aster
),
latest_batch AS (
  SELECT batch.*
  FROM control.activation_batches AS batch
  JOIN target_account AS account ON account.account_id = batch.account_id
  ORDER BY COALESCE(batch.completed_at, batch.created_at) DESC
  LIMIT 1
)
SELECT
  account.account_number,
  batch.batch_number,
  batch.status AS batch_status,
  item.item_number,
  subscription.subscription_number,
  sim.iccid,
  mdn.mdn,
  item.network_status AS flowone_status,
  item.flowone_activation_id,
  element.sequence_number AS flowone_step,
  element.element AS network_element,
  element.operation,
  element.provisioning_status,
  element.rollback_status,
  item.legacy_status AS legacy_billing_status,
  item.legacy_action_id,
  legacy_line.legacy_line_ref,
  legacy_line.status AS legacy_line_status
FROM latest_batch AS batch
JOIN target_account AS account ON account.account_id = batch.account_id
JOIN control.activation_batch_items AS item ON item.batch_id = batch.batch_id
JOIN iot.subscriptions AS subscription
  ON subscription.subscription_id = item.subscription_id
JOIN iot.sim_inventory AS sim ON sim.sim_resource_id = item.sim_resource_id
JOIN iot.mdn_inventory AS mdn ON mdn.mdn_resource_id = item.mdn_resource_id
LEFT JOIN control.flowone_element_results AS element
  ON element.batch_item_id = item.batch_item_id
LEFT JOIN legacy.lines AS legacy_line
  ON legacy_line.source_subscription_id = subscription.subscription_id
ORDER BY item.item_number, element.sequence_number;
