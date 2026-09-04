-- IoT Connect operational schema for PostgreSQL.
-- Idempotent: applied on first database creation and re-applied at every
-- application start by scripts/bootstrap_database.py.

CREATE SCHEMA IF NOT EXISTS iot;
CREATE SCHEMA IF NOT EXISTS legacy;
CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS catalog;

CREATE SEQUENCE IF NOT EXISTS control.customer_number_seq START WITH 300;
CREATE SEQUENCE IF NOT EXISTS control.account_number_seq START WITH 300;
CREATE SEQUENCE IF NOT EXISTS control.contract_number_seq START WITH 300;
CREATE SEQUENCE IF NOT EXISTS control.subscription_number_seq START WITH 1;
CREATE SEQUENCE IF NOT EXISTS control.batch_number_seq START WITH 1;
CREATE SEQUENCE IF NOT EXISTS control.bill_run_number_seq START WITH 1;

CREATE TABLE IF NOT EXISTS iot.customers (
  customer_id text PRIMARY KEY,
  customer_number text NOT NULL UNIQUE,
  customer_name text NOT NULL,
  status text NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS iot.accounts (
  account_id text PRIMARY KEY,
  account_number text NOT NULL UNIQUE,
  account_name text NOT NULL,
  customer_id text NOT NULL,
  contract_id text NOT NULL,
  external_customer_ref text,
  external_billing_account_number text NOT NULL,
  send_subscriptions_to_amdocs boolean NOT NULL,
  private_apn_name text,
  billing_mode text NOT NULL,
  status text NOT NULL,
  updated_by text NOT NULL,
  updated_at timestamptz NOT NULL
);

ALTER TABLE iot.accounts
  ADD COLUMN IF NOT EXISTS private_apn_name text;

CREATE INDEX IF NOT EXISTS accounts_customer_idx
  ON iot.accounts (customer_id);

CREATE INDEX IF NOT EXISTS accounts_contract_idx
  ON iot.accounts (contract_id);

CREATE INDEX IF NOT EXISTS accounts_external_billing_idx
  ON iot.accounts (external_billing_account_number);

CREATE TABLE IF NOT EXISTS iot.contracts (
  contract_id text PRIMARY KEY,
  contract_number text NOT NULL UNIQUE,
  account_id text NOT NULL,
  contract_name text NOT NULL,
  status text NOT NULL,
  effective_date date NOT NULL
);

CREATE TABLE IF NOT EXISTS iot.subscriptions (
  subscription_id text PRIMARY KEY,
  subscription_number text NOT NULL UNIQUE,
  source_subscription_ref text NOT NULL,
  account_id text NOT NULL,
  account_number text NOT NULL,
  contract_id text NOT NULL,
  product_offering_id text NOT NULL,
  price_plan_id text NOT NULL,
  technical_profile_id text,
  status text NOT NULL,
  start_date date NOT NULL,
  end_date date,
  activated_at timestamptz,
  source_batch_id text NOT NULL,
  source_batch_number text NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  UNIQUE (account_id, source_subscription_ref)
);

CREATE INDEX IF NOT EXISTS subscriptions_account_idx
  ON iot.subscriptions (account_id, subscription_number);

CREATE TABLE IF NOT EXISTS iot.sim_inventory (
  sim_resource_id text PRIMARY KEY,
  iccid text NOT NULL UNIQUE,
  imsi text NOT NULL UNIQUE,
  current_owner_type text NOT NULL,
  current_owner_ref text NOT NULL,
  resource_status text NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS sim_inventory_owner_idx
  ON iot.sim_inventory (current_owner_type, current_owner_ref, iccid);

CREATE TABLE IF NOT EXISTS iot.mdn_inventory (
  mdn_resource_id text PRIMARY KEY,
  mdn text NOT NULL UNIQUE,
  allocation_sequence bigint NOT NULL UNIQUE,
  status text NOT NULL,
  assigned_account_id text,
  updated_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS mdn_inventory_status_idx
  ON iot.mdn_inventory (status, allocation_sequence);

CREATE TABLE IF NOT EXISTS iot.subscription_resources (
  subscription_resource_id text PRIMARY KEY,
  subscription_id text NOT NULL,
  resource_type text NOT NULL,
  resource_id text NOT NULL,
  resource_role text NOT NULL,
  status text NOT NULL,
  effective_from timestamptz NOT NULL,
  effective_to timestamptz,
  UNIQUE (subscription_id, resource_type, resource_id)
);

CREATE INDEX IF NOT EXISTS subscription_resources_resource_idx
  ON iot.subscription_resources (resource_type, resource_id);

CREATE INDEX IF NOT EXISTS subscription_resources_subscription_idx
  ON iot.subscription_resources (subscription_id, resource_type);

CREATE TABLE IF NOT EXISTS catalog.product_offerings (
  product_offering_id text PRIMARY KEY,
  offering_code text NOT NULL UNIQUE,
  name text NOT NULL,
  fulfillment_type text NOT NULL,
  status text NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.rate_plans (
  rate_plan_id text PRIMARY KEY,
  product_offering_id text NOT NULL,
  rate_plan_code text NOT NULL UNIQUE,
  name text NOT NULL,
  monthly_price numeric(18,2) NOT NULL,
  gl_code text NOT NULL,
  currency text NOT NULL,
  status text NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.network_profiles (
  technical_profile_id text PRIMARY KEY,
  profile_code text NOT NULL UNIQUE,
  name text NOT NULL,
  service_package text NOT NULL,
  roaming_package text NOT NULL,
  status text NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.offering_resource_requirements (
  requirement_id text PRIMARY KEY,
  product_offering_id text NOT NULL,
  resource_type text NOT NULL,
  required boolean NOT NULL,
  allocation_method text NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy.accounts (
  legacy_account_ref text PRIMARY KEY,
  account_name text NOT NULL,
  status text NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy.lines (
  legacy_line_id text PRIMARY KEY,
  legacy_line_ref text NOT NULL UNIQUE,
  legacy_account_ref text NOT NULL,
  source_subscription_id text,
  mdn text,
  line_type text NOT NULL,
  status text NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS legacy_lines_account_idx
  ON legacy.lines (legacy_account_ref, legacy_line_ref);

CREATE TABLE IF NOT EXISTS iot.charges (
  charge_id text PRIMARY KEY,
  bill_run_id text NOT NULL,
  account_id text NOT NULL,
  contract_id text NOT NULL,
  subscription_id text,
  subscription_number text,
  bill_cycle text NOT NULL,
  charge_level text NOT NULL,
  charge_code text NOT NULL,
  rate_plan_id text,
  rate_plan_code text,
  description text NOT NULL,
  charge_type text NOT NULL,
  quantity numeric(18,6) NOT NULL,
  unit_price numeric(18,2) NOT NULL,
  amount numeric(18,2) NOT NULL,
  gl_code text NOT NULL,
  currency text NOT NULL
);

CREATE INDEX IF NOT EXISTS charges_bill_run_idx ON iot.charges (bill_run_id);

CREATE TABLE IF NOT EXISTS legacy.billing_rows (
  billing_row_id text PRIMARY KEY,
  bill_run_id text NOT NULL,
  row_number bigint NOT NULL,
  bill_cycle text NOT NULL,
  account_id text NOT NULL,
  account_number text NOT NULL,
  contract_id text NOT NULL,
  legacy_account_ref text NOT NULL,
  target_line_ref text NOT NULL,
  source_charge_level text NOT NULL,
  posting_scope text NOT NULL,
  mdn text,
  charge_code text NOT NULL,
  rate_plan_id text,
  rate_plan_code text,
  description text NOT NULL,
  charge_type text NOT NULL,
  quantity numeric(18,6) NOT NULL,
  unit_price numeric(18,2) NOT NULL,
  amount numeric(18,2) NOT NULL,
  gl_code text NOT NULL,
  currency text NOT NULL,
  source_record_count bigint NOT NULL,
  source_charge_ids text[] NOT NULL,
  UNIQUE (bill_run_id, row_number)
);

CREATE INDEX IF NOT EXISTS billing_rows_bill_run_idx
  ON legacy.billing_rows (bill_run_id, row_number);

-- Direct integration-action resources returned by
-- POST /api/v1/network-activations and POST /api/v1/legacy-subscription-actions.
-- The complete response body is stored so GET returns it unchanged after restart.
CREATE TABLE IF NOT EXISTS control.network_activations (
  activation_id text PRIMARY KEY,
  correlation_id text NOT NULL,
  wdh_service_status text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS control.legacy_subscription_actions (
  compatibility_action_id text PRIMARY KEY,
  wdh_status text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS control.audit_events (
  audit_id text PRIMARY KEY,
  account_id text NOT NULL,
  event_type text NOT NULL,
  actor text NOT NULL,
  reason text NOT NULL,
  details jsonb NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS control.activation_events (
  event_id text PRIMARY KEY,
  batch_id text NOT NULL,
  batch_number text NOT NULL,
  account_id text NOT NULL,
  contract_id text NOT NULL,
  subscription_id text NOT NULL,
  source_subscription_ref text NOT NULL,
  iot_outcome text NOT NULL,
  legacy_outcome text NOT NULL,
  actor text NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS control.activation_batches (
  batch_id text PRIMARY KEY,
  batch_number text NOT NULL UNIQUE,
  account_id text NOT NULL,
  status text NOT NULL,
  item_count bigint NOT NULL,
  success_count bigint NOT NULL,
  failure_count bigint NOT NULL,
  actor text NOT NULL,
  created_at timestamptz NOT NULL,
  submitted_at timestamptz,
  completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS activation_batches_account_idx
  ON control.activation_batches (account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS control.activation_batch_items (
  batch_item_id text PRIMARY KEY,
  batch_id text NOT NULL,
  item_number bigint NOT NULL,
  source_order_ref text NOT NULL,
  subscription_id text NOT NULL,
  sim_resource_id text NOT NULL,
  mdn_resource_id text NOT NULL,
  private_apn text,
  network_status text NOT NULL,
  flowone_activation_id text,
  legacy_status text NOT NULL,
  legacy_action_id text,
  overall_status text NOT NULL,
  message text NOT NULL,
  created_at timestamptz NOT NULL,
  completed_at timestamptz,
  UNIQUE (batch_id, item_number)
);

ALTER TABLE control.activation_batch_items
  ADD COLUMN IF NOT EXISTS private_apn text;

CREATE INDEX IF NOT EXISTS activation_batch_items_batch_idx
  ON control.activation_batch_items (batch_id, item_number);

CREATE TABLE IF NOT EXISTS control.flowone_element_results (
  element_result_id text PRIMARY KEY,
  batch_item_id text NOT NULL,
  sequence_number bigint NOT NULL,
  element text NOT NULL,
  operation text NOT NULL,
  provisioning_status text NOT NULL,
  element_code text NOT NULL,
  message text NOT NULL,
  rollback_status text NOT NULL,
  applied_profile text,
  recorded_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS flowone_results_item_idx
  ON control.flowone_element_results (batch_item_id, sequence_number);

CREATE TABLE IF NOT EXISTS control.bill_runs (
  bill_run_id text PRIMARY KEY,
  bill_run_number text NOT NULL UNIQUE,
  account_id text NOT NULL,
  account_number text NOT NULL,
  contract_id text NOT NULL,
  account_name text NOT NULL,
  billing_mode text NOT NULL,
  bill_cycle text NOT NULL,
  status text NOT NULL,
  source_charge_count bigint NOT NULL,
  output_row_count bigint NOT NULL,
  source_total numeric(18,2) NOT NULL,
  output_total numeric(18,2) NOT NULL,
  variance numeric(18,2) NOT NULL,
  unrepresented_source_records bigint NOT NULL,
  duplicate_source_representations bigint NOT NULL,
  invalid_target_lines bigint NOT NULL,
  actor text NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS bill_runs_account_cycle_idx
  ON control.bill_runs (account_id, bill_cycle, created_at DESC);

INSERT INTO catalog.product_offerings
  (product_offering_id, offering_code, name, fulfillment_type, status)
VALUES
  ('OFFER-IOT-CONNECTIVITY','IOT-CONNECT','Managed IoT Connectivity','FLOWONE_NETWORK_ACTIVATION','ACTIVE'),
  ('OFFER-NETFLIX-PREMIUM','VAS-NETFLIX-PREMIUM','Netflix Premium Add-on','PARTNER_FULFILLMENT','ACTIVE'),
  ('OFFER-SHARED-DATA-POOL','IOT-SHARED-DATA','Enterprise Shared Data Pool','ACCOUNT_CONFIGURATION','ACTIVE')
ON CONFLICT (product_offering_id) DO UPDATE SET
  offering_code = EXCLUDED.offering_code,
  name = EXCLUDED.name,
  fulfillment_type = EXCLUDED.fulfillment_type,
  status = EXCLUDED.status;

INSERT INTO catalog.rate_plans
  (rate_plan_id, product_offering_id, rate_plan_code, name, monthly_price, gl_code, currency, status)
VALUES
  ('PLAN-IOT-001','OFFER-IOT-CONNECTIVITY','RP1','Connected Device Essential',2.00,'4100-IOT-RP1','USD','ACTIVE'),
  ('PLAN-IOT-002','OFFER-IOT-CONNECTIVITY','RP2','Connected Device Enhanced',3.00,'4100-IOT-RP2','USD','ACTIVE'),
  ('PLAN-IOT-003','OFFER-IOT-CONNECTIVITY','RP3','Connected Device Premium',5.00,'4100-IOT-RP3','USD','ACTIVE'),
  ('PLAN-IOT-004','OFFER-IOT-CONNECTIVITY','RP4','Critical Asset Managed',8.00,'4100-IOT-RP4','USD','ACTIVE'),
  ('PLAN-VAS-NETFLIX-PREMIUM','OFFER-NETFLIX-PREMIUM','VAS-NFX-PREM','Netflix Premium Monthly',22.99,'4110-VAS-NETFLIX','USD','ACTIVE'),
  ('PLAN-IOT-SHARED-100GB','OFFER-SHARED-DATA-POOL','POOL-100GB','Enterprise Shared Data Pool — 100 GB',100.00,'4120-IOT-DATA-POOL','USD','ACTIVE')
ON CONFLICT (rate_plan_id) DO UPDATE SET
  product_offering_id = EXCLUDED.product_offering_id,
  rate_plan_code = EXCLUDED.rate_plan_code,
  name = EXCLUDED.name,
  monthly_price = EXCLUDED.monthly_price,
  gl_code = EXCLUDED.gl_code,
  currency = EXCLUDED.currency,
  status = EXCLUDED.status;

INSERT INTO catalog.network_profiles
  (technical_profile_id, profile_code, name, service_package, roaming_package, status)
VALUES
  ('NET-DATA-SMS-DOM','DATA_SMS_DOMESTIC','Data and SMS — Domestic Roaming','DATA_SMS','DOMESTIC','ACTIVE'),
  ('NET-DATA-HOME','DATA_HOME_ONLY','Data Only — Home Network','DATA_ONLY','HOME_ONLY','ACTIVE')
ON CONFLICT (technical_profile_id) DO UPDATE SET
  profile_code = EXCLUDED.profile_code,
  name = EXCLUDED.name,
  service_package = EXCLUDED.service_package,
  roaming_package = EXCLUDED.roaming_package,
  status = EXCLUDED.status;

INSERT INTO catalog.offering_resource_requirements
  (requirement_id, product_offering_id, resource_type, required, allocation_method)
VALUES
  ('REQ-IOT-SIM','OFFER-IOT-CONNECTIVITY','SIM',TRUE,'CUSTOMER_SELECTED'),
  ('REQ-IOT-MDN','OFFER-IOT-CONNECTIVITY','MDN',TRUE,'NEXT_AVAILABLE')
ON CONFLICT (requirement_id) DO UPDATE SET
  product_offering_id = EXCLUDED.product_offering_id,
  resource_type = EXCLUDED.resource_type,
  required = EXCLUDED.required,
  allocation_method = EXCLUDED.allocation_method;
