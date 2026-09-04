-- IoT Connect interview evidence
-- Run either statement independently in TablePlus.
-- Change only the account number in the first CTE/WHERE clause when needed.

-- 1. Newest subscriptions activated for one customer.
-- This reads the operational subscription table directly, then adds the
-- activation-batch and FlowOne outcomes that created each subscription.
SELECT
  account.account_number,
  account.account_name,
  subscription.subscription_number,
  subscription.status AS subscription_status,
  subscription.activated_at,
  sim.iccid,
  sim.imsi,
  mdn.mdn,
  batch.batch_number,
  batch.status AS batch_status,
  item.network_status AS flowone_status,
  item.legacy_status AS legacy_billing_status,
  item.overall_status,
  item.message,
  item.flowone_activation_id
FROM iot.subscriptions AS subscription
JOIN iot.accounts AS account ON account.account_id = subscription.account_id
JOIN control.activation_batch_items AS item
  ON item.subscription_id = subscription.subscription_id
JOIN control.activation_batches AS batch ON batch.batch_id = item.batch_id
JOIN iot.sim_inventory AS sim ON sim.sim_resource_id = item.sim_resource_id
JOIN iot.mdn_inventory AS mdn ON mdn.mdn_resource_id = item.mdn_resource_id
WHERE account.account_number = 'ACCT-000100' -- Aster Field Systems
ORDER BY subscription.activated_at DESC
LIMIT 10;

-- 2. Current summarized-billing policy for one customer.
SELECT
  account_number,
  account_name,
  billing_mode,
  (billing_mode = 'SUMMARIZED') AS summarized_billing_enabled,
  send_subscriptions_to_amdocs AS send_subscriptions_to_legacy_billing,
  CASE
    WHEN billing_mode = 'SUMMARIZED' THEN 'ACCOUNT'
    ELSE 'SUBSCRIPTION'
  END AS posting_scope,
  external_billing_account_number,
  updated_by,
  updated_at
FROM iot.accounts
WHERE account_number = 'ACCT-000200'; -- Boreal Equipment Group
