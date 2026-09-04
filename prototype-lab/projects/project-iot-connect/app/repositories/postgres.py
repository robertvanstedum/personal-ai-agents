from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

import psycopg
from psycopg import sql
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.demo_seed import build_demo_seed
from app.domain.errors import ConflictError


INTEGER_DECIMAL_FIELDS = {
    "quantity",
}


def _normalize(value: Any, field_name: str | None = None) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if field_name in INTEGER_DECIMAL_FIELDS and value == value.to_integral_value():
            return int(value)
        return format(value, "f")
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item, key) for key, item in value.items()}
    return value


def _adapt(value: Any) -> Any:
    if isinstance(value, dict):
        return Jsonb(value)
    return value


class PostgresRepository:
    """Transactional operational store shared by the Mac and AWS runtimes."""

    backend_name = "postgres"

    SEQUENCES = {
        "customer": "control.customer_number_seq",
        "account": "control.account_number_seq",
        "contract": "control.contract_number_seq",
        "subscription": "control.subscription_number_seq",
        "batch": "control.batch_number_seq",
        "bill_run": "control.bill_run_number_seq",
    }

    DATA_TABLES = (
        "control.network_activations",
        "control.legacy_subscription_actions",
        "legacy.billing_rows",
        "iot.charges",
        "control.bill_runs",
        "control.activation_events",
        "control.flowone_element_results",
        "control.activation_batch_items",
        "control.activation_batches",
        "control.audit_events",
        "iot.subscription_resources",
        "iot.subscriptions",
        "iot.sim_inventory",
        "iot.mdn_inventory",
        "legacy.lines",
        "legacy.accounts",
        "iot.contracts",
        "iot.accounts",
        "iot.customers",
    )

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn.strip()
        self._state = threading.local()

    @classmethod
    def from_environment(cls) -> "PostgresRepository":
        dsn = os.getenv("POSTGRES_DSN")
        if not dsn:
            raise RuntimeError("Missing PostgreSQL setting: POSTGRES_DSN")
        return cls(dsn)

    @staticmethod
    def _table(name: str) -> sql.Composed:
        return sql.SQL(".").join(sql.Identifier(part) for part in name.split("."))

    @contextmanager
    def _connection(self):
        active = getattr(self._state, "connection", None)
        if active is not None:
            yield active
            return
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            yield connection

    @contextmanager
    def transaction(self):
        if getattr(self._state, "connection", None) is not None:
            raise RuntimeError("Nested repository transactions are not supported")
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            self._state.connection = connection
            try:
                with connection.transaction():
                    yield
            finally:
                self._state.connection = None

    def _execute(self, statement, parameters: Iterable[Any] = ()) -> None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(statement, tuple(parameters))

    def _fetch_all(self, statement, parameters: Iterable[Any] = ()) -> list[dict]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(statement, tuple(parameters))
            return [_normalize(dict(row)) for row in cursor.fetchall()]

    def _fetch_one(self, statement, parameters: Iterable[Any] = ()) -> dict | None:
        rows = self._fetch_all(statement, parameters)
        return rows[0] if rows else None

    def _insert(self, table: str, row: dict) -> None:
        columns = list(row)
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            self._table(table),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        try:
            self._execute(statement, [_adapt(row[column]) for column in columns])
        except UniqueViolation as exc:
            raise ConflictError(f"A unique value already exists in {table}") from exc

    def _insert_many(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        columns = list(rows[0])
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            self._table(table),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        parameters = [
            tuple(_adapt(row[column]) for column in columns)
            for row in rows
        ]
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.executemany(statement, parameters)

    def _update(self, table: str, row: dict, key: str, fields: Iterable[str]) -> None:
        fields = list(fields)
        statement = sql.SQL("UPDATE {} SET {} WHERE {} = {}").format(
            self._table(table),
            sql.SQL(", ").join(
                sql.SQL("{} = {}").format(sql.Identifier(field), sql.Placeholder())
                for field in fields
            ),
            sql.Identifier(key),
            sql.Placeholder(),
        )
        self._execute(
            statement,
            [_adapt(row[field]) for field in fields] + [_adapt(row[key])],
        )

    def reset(self) -> dict:
        seed = build_demo_seed()

        with self.transaction():
            table_list = sql.SQL(", ").join(self._table(name) for name in self.DATA_TABLES)
            self._execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY").format(table_list))
            for name, sequence in self.SEQUENCES.items():
                restart = 300 if name in {"customer", "account", "contract"} else 1
                self._execute(
                    sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
                        self._table(sequence), sql.Literal(restart)
                    )
                )

            self._insert_many("iot.customers", seed["customers"])
            self._insert_many("iot.accounts", seed["accounts"])
            self._insert_many("iot.contracts", seed["contracts"])
            self._insert_many("legacy.accounts", seed["legacy_accounts"])
            self._insert_many("iot.sim_inventory", seed["sims"])
            self._insert_many("iot.mdn_inventory", seed["mdns"])

        return {
            "status": "reset",
            "backend": self.backend_name,
            "accounts_seeded": 2,
            "contracts_seeded": 2,
            "golden_lines_seeded": 0,
            "sim_resources_seeded": 1000,
            "mdn_resources_seeded": 1000,
        }

    def next_number(self, sequence_name: str) -> int:
        sequence = self.SEQUENCES.get(sequence_name)
        if not sequence:
            raise KeyError(f"Unknown sequence {sequence_name}")
        row = self._fetch_one(
            sql.SQL("SELECT nextval({}) AS value").format(sql.Literal(sequence))
        )
        return int(row["value"])

    def list_accounts(self) -> list[dict]:
        return self._fetch_all("SELECT * FROM iot.accounts ORDER BY account_number")

    def get_account(self, account_id: str) -> dict | None:
        return self._fetch_one("SELECT * FROM iot.accounts WHERE account_id = %s", (account_id,))

    def insert_account(self, row: dict) -> None:
        self._insert("iot.accounts", row)

    def update_account(self, row: dict) -> None:
        self._update(
            "iot.accounts",
            row,
            "account_id",
            (
                "account_name", "external_customer_ref",
                "external_billing_account_number", "send_subscriptions_to_amdocs",
                "private_apn_name", "billing_mode", "status", "updated_by", "updated_at",
            ),
        )

    def insert_customer(self, row: dict) -> None:
        self._insert("iot.customers", row)

    def get_customer(self, customer_id: str) -> dict | None:
        return self._fetch_one("SELECT * FROM iot.customers WHERE customer_id = %s", (customer_id,))

    def update_customer(self, row: dict) -> None:
        self._update(
            "iot.customers",
            row,
            "customer_id",
            ("customer_name", "status"),
        )

    def get_contract(self, contract_id: str) -> dict | None:
        return self._fetch_one("SELECT * FROM iot.contracts WHERE contract_id = %s", (contract_id,))

    def insert_contract(self, row: dict) -> None:
        self._insert("iot.contracts", row)

    def insert_legacy_account(self, row: dict) -> None:
        self._insert("legacy.accounts", row)

    def get_legacy_account(self, legacy_account_ref: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM legacy.accounts WHERE legacy_account_ref = %s",
            (legacy_account_ref,),
        )

    def list_legacy_accounts(self) -> list[dict]:
        return self._fetch_all("SELECT * FROM legacy.accounts ORDER BY legacy_account_ref")

    def insert_legacy_line(self, row: dict) -> None:
        if self.get_legacy_line(row["legacy_line_ref"]):
            raise ConflictError(f"legacy_line_ref {row['legacy_line_ref']} already exists")
        self._insert("legacy.lines", row)

    def get_legacy_line(self, legacy_line_ref: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM legacy.lines WHERE legacy_line_ref = %s",
            (legacy_line_ref,),
        )

    def list_legacy_lines(self, legacy_account_ref: str) -> list[dict]:
        return self._fetch_all(
            "SELECT * FROM legacy.lines WHERE legacy_account_ref = %s ORDER BY legacy_line_ref",
            (legacy_account_ref,),
        )

    def find_subscription_by_source(self, account_id: str, source_subscription_ref: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM iot.subscriptions WHERE account_id = %s AND source_subscription_ref = %s",
            (account_id, source_subscription_ref),
        )

    def find_subscription_by_sim(self, sim_id: str) -> dict | None:
        return self._fetch_one(
            "SELECT s.* FROM iot.subscriptions s "
            "JOIN iot.subscription_resources r ON r.subscription_id = s.subscription_id "
            "WHERE r.resource_type = 'SIM' AND r.resource_id = %s",
            (sim_id,),
        )

    def insert_subscription(self, row: dict) -> None:
        self._insert("iot.subscriptions", row)

    def update_subscription(self, row: dict) -> None:
        self._update(
            "iot.subscriptions",
            row,
            "subscription_id",
            ("status", "end_date", "activated_at", "updated_at"),
        )

    def list_subscriptions(self, account_id: str) -> list[dict]:
        return self._fetch_all(
            "SELECT * FROM iot.subscriptions WHERE account_id = %s ORDER BY subscription_number",
            (account_id,),
        )

    def list_sims(self, account_id: str | None = None) -> list[dict]:
        if account_id is None:
            return self._fetch_all("SELECT * FROM iot.sim_inventory ORDER BY iccid")
        return self._fetch_all(
            "SELECT * FROM iot.sim_inventory "
            "WHERE current_owner_type = 'ACCOUNT' AND current_owner_ref = %s ORDER BY iccid",
            (account_id,),
        )

    def list_account_resource_views(self, account_id: str) -> list[dict]:
        return self._fetch_all(
            "SELECT sim.*, sub.subscription_id, sub.subscription_number, "
            "sub.price_plan_id, sub.status AS subscription_status, "
            "sub.updated_at AS subscription_updated_at, "
            "mdn.mdn_resource_id, mdn.mdn "
            "FROM iot.sim_inventory sim "
            "LEFT JOIN iot.subscription_resources sim_link "
            "ON sim_link.resource_type = 'SIM' "
            "AND sim_link.resource_id = sim.sim_resource_id "
            "AND sim_link.status = 'ACTIVE' "
            "LEFT JOIN iot.subscriptions sub "
            "ON sub.subscription_id = sim_link.subscription_id "
            "AND sub.account_id = %s "
            "LEFT JOIN iot.subscription_resources mdn_link "
            "ON mdn_link.subscription_id = sub.subscription_id "
            "AND mdn_link.resource_type = 'MDN' "
            "AND mdn_link.status = 'ACTIVE' "
            "LEFT JOIN iot.mdn_inventory mdn "
            "ON mdn.mdn_resource_id = mdn_link.resource_id "
            "WHERE sim.current_owner_type = 'ACCOUNT' "
            "AND sim.current_owner_ref = %s "
            "ORDER BY sim.iccid",
            (account_id, account_id),
        )

    def get_sim(self, sim_resource_id: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM iot.sim_inventory WHERE sim_resource_id = %s",
            (sim_resource_id,),
        )

    def insert_sim(self, row: dict) -> None:
        self._insert("iot.sim_inventory", row)

    def update_sim(self, row: dict) -> None:
        self._update(
            "iot.sim_inventory",
            row,
            "sim_resource_id",
            ("current_owner_type", "current_owner_ref", "resource_status", "updated_at"),
        )

    def list_mdns(self, status: str | None = None) -> list[dict]:
        if status is None:
            return self._fetch_all("SELECT * FROM iot.mdn_inventory ORDER BY allocation_sequence")
        return self._fetch_all(
            "SELECT * FROM iot.mdn_inventory WHERE status = %s ORDER BY allocation_sequence",
            (status,),
        )

    def get_mdn(self, mdn_resource_id: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM iot.mdn_inventory WHERE mdn_resource_id = %s",
            (mdn_resource_id,),
        )

    def update_mdn(self, row: dict) -> None:
        self._update(
            "iot.mdn_inventory",
            row,
            "mdn_resource_id",
            ("status", "assigned_account_id", "updated_at"),
        )

    def insert_subscription_resource(self, row: dict) -> None:
        self._insert("iot.subscription_resources", row)

    def update_subscription_resource(self, row: dict) -> None:
        self._update(
            "iot.subscription_resources",
            row,
            "subscription_resource_id",
            ("status", "effective_to"),
        )

    def list_subscription_resources(self, subscription_id: str) -> list[dict]:
        return self._fetch_all(
            "SELECT * FROM iot.subscription_resources WHERE subscription_id = %s",
            (subscription_id,),
        )

    def insert_activation_batch(self, row: dict) -> None:
        self._insert("control.activation_batches", row)

    def update_activation_batch(self, row: dict) -> None:
        self._update(
            "control.activation_batches",
            row,
            "batch_id",
            ("status", "success_count", "failure_count", "submitted_at", "completed_at"),
        )

    def get_activation_batch(self, batch_id: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM control.activation_batches WHERE batch_id = %s",
            (batch_id,),
        )

    def list_activation_batches(self, account_id: str | None = None) -> list[dict]:
        if account_id is None:
            return self._fetch_all(
                "SELECT * FROM control.activation_batches ORDER BY created_at DESC"
            )
        return self._fetch_all(
            "SELECT * FROM control.activation_batches WHERE account_id = %s ORDER BY created_at DESC",
            (account_id,),
        )

    def insert_activation_batch_item(self, row: dict) -> None:
        self._insert("control.activation_batch_items", row)

    def update_activation_batch_item(self, row: dict) -> None:
        self._update(
            "control.activation_batch_items",
            row,
            "batch_item_id",
            (
                "network_status", "flowone_activation_id", "legacy_status",
                "legacy_action_id", "overall_status", "message", "completed_at",
            ),
        )

    def list_activation_batch_items(self, batch_id: str) -> list[dict]:
        return self._fetch_all(
            "SELECT * FROM control.activation_batch_items WHERE batch_id = %s ORDER BY item_number",
            (batch_id,),
        )

    def insert_flowone_element_result(self, row: dict) -> None:
        self._insert("control.flowone_element_results", row)

    def list_flowone_element_results(self, batch_item_id: str) -> list[dict]:
        return self._fetch_all(
            "SELECT * FROM control.flowone_element_results "
            "WHERE batch_item_id = %s ORDER BY sequence_number",
            (batch_item_id,),
        )

    def insert_network_activation(self, row: dict) -> None:
        self._insert("control.network_activations", row)

    def get_network_activation(self, activation_id: str) -> dict | None:
        row = self._fetch_one(
            "SELECT payload FROM control.network_activations WHERE activation_id = %s",
            (activation_id,),
        )
        return row["payload"] if row else None

    def insert_legacy_subscription_action(self, row: dict) -> None:
        self._insert("control.legacy_subscription_actions", row)

    def get_legacy_subscription_action(self, compatibility_action_id: str) -> dict | None:
        row = self._fetch_one(
            "SELECT payload FROM control.legacy_subscription_actions "
            "WHERE compatibility_action_id = %s",
            (compatibility_action_id,),
        )
        return row["payload"] if row else None

    def insert_audit_event(self, row: dict) -> None:
        self._insert("control.audit_events", row)

    def insert_activation_event(self, row: dict) -> None:
        self._insert("control.activation_events", row)

    def delete_billing_for_account_cycle(self, account_id: str, bill_cycle: str) -> None:
        predicate = "SELECT bill_run_id FROM control.bill_runs WHERE account_id = %s AND bill_cycle = %s"
        parameters = (account_id, bill_cycle)
        self._execute(
            f"DELETE FROM iot.charges WHERE bill_run_id IN ({predicate})",
            parameters,
        )
        self._execute(
            f"DELETE FROM legacy.billing_rows WHERE bill_run_id IN ({predicate})",
            parameters,
        )
        self._execute(
            "DELETE FROM control.bill_runs WHERE account_id = %s AND bill_cycle = %s",
            parameters,
        )

    def insert_charge(self, row: dict) -> None:
        self._insert("iot.charges", row)

    def insert_billing_row(self, row: dict) -> None:
        self._insert("legacy.billing_rows", row)

    def insert_bill_run(self, row: dict) -> None:
        self._insert("control.bill_runs", row)

    def get_bill_run(self, bill_run_id: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM control.bill_runs WHERE bill_run_id = %s",
            (bill_run_id,),
        )

    def list_bill_runs(self, account_id: str | None = None) -> list[dict]:
        if account_id is None:
            return self._fetch_all(
                "SELECT * FROM control.bill_runs ORDER BY created_at DESC"
            )
        return self._fetch_all(
            "SELECT * FROM control.bill_runs WHERE account_id = %s ORDER BY created_at DESC",
            (account_id,),
        )

    def list_charges(self, bill_run_id: str) -> list[dict]:
        return self._fetch_all(
            "SELECT * FROM iot.charges WHERE bill_run_id = %s ORDER BY charge_level, charge_id",
            (bill_run_id,),
        )

    def list_billing_rows(self, bill_run_id: str) -> list[dict]:
        return self._fetch_all(
            "SELECT * FROM legacy.billing_rows WHERE bill_run_id = %s ORDER BY row_number",
            (bill_run_id,),
        )

    def latest_bill_run(self, account_id: str, bill_cycle: str) -> dict | None:
        return self._fetch_one(
            "SELECT * FROM control.bill_runs WHERE account_id = %s AND bill_cycle = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, bill_cycle),
        )
