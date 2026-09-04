from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from app.domain.errors import ConflictError
from app.domain.identity import seeded_uuid


def sql_value(value: Any) -> str:
    """Encode values; user-supplied strings never become executable SQL."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (list, dict)):
        payload = json.dumps(value, separators=(",", ":")).replace("'", "''")
        return f"PARSE_JSON('{payload}')"
    return "'" + str(value).replace("'", "''") + "'"


def values(*items: Any) -> str:
    return ", ".join(sql_value(item) for item in items)


class SnowflakeSqlApi:
    """Small SQL API client; PAT remains on the backend only."""

    def __init__(
        self,
        *,
        account_identifier: str,
        pat: str,
        warehouse: str,
        database: str,
        schema: str,
        role: str,
        timeout: int = 60,
    ) -> None:
        self.base_url = f"https://{account_identifier}.snowflakecomputing.com"
        self.pat = pat
        self.warehouse = warehouse
        self.database = database
        self.schema = schema
        self.role = role
        self.timeout = timeout

    def _request(self, url: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method="POST" if payload is not None else "GET",
            headers={
                "Authorization": f"Bearer {self.pat}",
                "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("message", raw)
            except json.JSONDecodeError:
                detail = raw
            raise RuntimeError(f"Snowflake SQL API HTTP {exc.code}: {detail}") from exc

    def execute(self, statement: str, multi_count: int | None = None) -> list[dict]:
        payload: dict[str, Any] = {
            "statement": statement,
            "timeout": self.timeout,
            "database": self.database,
            "schema": self.schema,
            "warehouse": self.warehouse,
            "role": self.role,
        }
        if multi_count is not None:
            payload["parameters"] = {"MULTI_STATEMENT_COUNT": str(multi_count)}
        result = self._request(f"{self.base_url}/api/v2/statements", payload)
        status_url = result.get("statementStatusUrl")
        deadline = time.monotonic() + self.timeout
        while result.get("code") in {"333333", "333334"} and status_url:
            if time.monotonic() >= deadline:
                raise TimeoutError("Snowflake SQL API statement did not finish before timeout")
            time.sleep(0.25)
            result = self._request(f"{self.base_url}{status_url}")
        if result.get("sqlState") not in {None, "00000"}:
            raise RuntimeError(
                f"Snowflake SQL error {result.get('sqlState')}: {result.get('message')}"
            )
        return self._rows(result)

    def execute_many(self, statements: list[str]) -> None:
        if not statements:
            return
        script = ";\n".join(["BEGIN", *statements, "COMMIT"])
        self.execute(script, multi_count=len(statements) + 2)

    @staticmethod
    def _rows(result: dict) -> list[dict]:
        metadata = result.get("resultSetMetaData", {}).get("rowType", [])
        data = result.get("data", [])
        rows = []
        for source in data:
            row = {}
            for meta, raw in zip(metadata, source):
                row[meta["name"].lower()] = SnowflakeSqlApi._coerce(raw, meta)
            rows.append(row)
        return rows

    @staticmethod
    def _coerce(raw: Any, meta: dict) -> Any:
        if raw is None:
            return None
        data_type = meta.get("type")
        if data_type == "boolean":
            return str(raw).lower() == "true"
        if data_type == "fixed":
            scale = int(meta.get("scale") or 0)
            number = Decimal(str(raw))
            return int(number) if scale == 0 else f"{number:.{scale}f}"
        if data_type in {"array", "object", "variant"}:
            return json.loads(raw) if isinstance(raw, str) else raw
        return raw


class SnowflakeRepository:
    """Persistence only. Business behavior remains in DemoService."""

    backend_name = "snowflake"

    SEQUENCES = {
        "customer": "IOTCONNECT_POC.CONTROL.CUSTOMER_NUMBER_SEQ",
        "account": "IOTCONNECT_POC.CONTROL.ACCOUNT_NUMBER_SEQ",
        "contract": "IOTCONNECT_POC.CONTROL.CONTRACT_NUMBER_SEQ",
        "subscription": "IOTCONNECT_POC.CONTROL.SUBSCRIPTION_NUMBER_SEQ",
        "batch": "IOTCONNECT_POC.CONTROL.BATCH_NUMBER_SEQ",
        "bill_run": "IOTCONNECT_POC.CONTROL.BILL_RUN_NUMBER_SEQ",
    }

    def __init__(self, client: SnowflakeSqlApi) -> None:
        self.client = client
        self._pending: list[str] | None = None

    @classmethod
    def from_environment(cls) -> "SnowflakeRepository":
        required = {
            "SNOWFLAKE_ACCOUNT_IDENTIFIER": os.getenv("SNOWFLAKE_ACCOUNT_IDENTIFIER"),
            "SNOWFLAKE_PAT": os.getenv("SNOWFLAKE_PAT"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Snowflake settings: {', '.join(missing)}")
        return cls(
            SnowflakeSqlApi(
                account_identifier=required["SNOWFLAKE_ACCOUNT_IDENTIFIER"],
                pat=required["SNOWFLAKE_PAT"].strip(),
                warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "IOTCONNECT_WH"),
                database=os.getenv("SNOWFLAKE_DATABASE", "IOTCONNECT_POC"),
                schema=os.getenv("SNOWFLAKE_SCHEMA", "CONTROL"),
                role=os.getenv("SNOWFLAKE_ROLE", "IOTCONNECT_APP_ROLE"),
            )
        )

    @contextmanager
    def transaction(self):
        if self._pending is not None:
            raise RuntimeError("Nested repository transactions are not supported")
        self._pending = []
        try:
            yield
            self.client.execute_many(self._pending)
        finally:
            self._pending = None

    def _write(self, statement: str) -> None:
        if self._pending is None:
            self.client.execute(statement)
        else:
            self._pending.append(statement)

    def reset(self) -> dict:
        now = "CURRENT_TIMESTAMP()"
        aster_account_id = seeded_uuid("account", "ACCT-000100")
        boreal_account_id = seeded_uuid("account", "ACCT-000200")
        aster_customer_id = seeded_uuid("customer", "CUS-000100")
        boreal_customer_id = seeded_uuid("customer", "CUS-000200")
        aster_contract_id = seeded_uuid("contract", "CTR-000100")
        boreal_contract_id = seeded_uuid("contract", "CTR-000200")
        statements = [
            "DELETE FROM IOTCONNECT_POC.LEGACY.BILLING_ROWS",
            "DELETE FROM IOTCONNECT_POC.IOT.CHARGES",
            "DELETE FROM IOTCONNECT_POC.CONTROL.BILL_RUNS",
            "DELETE FROM IOTCONNECT_POC.CONTROL.ACTIVATION_EVENTS",
            "DELETE FROM IOTCONNECT_POC.CONTROL.FLOWONE_ELEMENT_RESULTS",
            "DELETE FROM IOTCONNECT_POC.CONTROL.ACTIVATION_BATCH_ITEMS",
            "DELETE FROM IOTCONNECT_POC.CONTROL.ACTIVATION_BATCHES",
            "DELETE FROM IOTCONNECT_POC.CONTROL.AUDIT_EVENTS",
            "DELETE FROM IOTCONNECT_POC.IOT.SUBSCRIPTION_RESOURCES",
            "DELETE FROM IOTCONNECT_POC.IOT.SUBSCRIPTIONS",
            "DELETE FROM IOTCONNECT_POC.IOT.SIM_INVENTORY",
            "DELETE FROM IOTCONNECT_POC.IOT.MDN_INVENTORY",
            "DELETE FROM IOTCONNECT_POC.LEGACY.LINES",
            "DELETE FROM IOTCONNECT_POC.LEGACY.ACCOUNTS",
            "DELETE FROM IOTCONNECT_POC.IOT.CONTRACTS",
            "DELETE FROM IOTCONNECT_POC.IOT.ACCOUNTS",
            "DELETE FROM IOTCONNECT_POC.IOT.CUSTOMERS",
            "INSERT INTO IOTCONNECT_POC.IOT.CUSTOMERS VALUES "
            f"({values(aster_customer_id, 'CUS-000100', 'Aster Field Systems', 'ACTIVE')}, {now}),"
            f"({values(boreal_customer_id, 'CUS-000200', 'Boreal Equipment Group', 'ACTIVE')}, {now})",
            "INSERT INTO IOTCONNECT_POC.IOT.ACCOUNTS "
            "(ACCOUNT_ID,ACCOUNT_NUMBER,ACCOUNT_NAME,CUSTOMER_ID,CONTRACT_ID,EXTERNAL_CUSTOMER_REF,EXTERNAL_BILLING_ACCOUNT_NUMBER,SEND_SUBSCRIPTIONS_TO_AMDOCS,PRIVATE_APN_NAME,BILLING_MODE,STATUS,UPDATED_BY,UPDATED_AT) VALUES "
            f"({values(aster_account_id, 'ACCT-000100', 'Aster Field Systems', aster_customer_id, aster_contract_id, 'CRM-ASTER-100', 'LEG-ACCT-100', True, None, 'DETAILED', 'ACTIVE', 'seed')}, {now}),"
            f"({values(boreal_account_id, 'ACCT-000200', 'Boreal Equipment Group', boreal_customer_id, boreal_contract_id, 'CRM-BOREAL-200', 'LEG-ACCT-200', True, 'BOREAL_IOT_PRIVATE', 'DETAILED', 'ACTIVE', 'seed')}, {now})",
            "INSERT INTO IOTCONNECT_POC.IOT.CONTRACTS VALUES "
            f"({values(aster_contract_id, 'CTR-000100', aster_account_id, 'Aster Enterprise IoT Agreement', 'ACTIVE', '2026-08-01')}),"
            f"({values(boreal_contract_id, 'CTR-000200', boreal_account_id, 'Boreal Enterprise IoT Agreement', 'ACTIVE', '2026-08-01')})",
            "INSERT INTO IOTCONNECT_POC.LEGACY.ACCOUNTS VALUES "
            "('LEG-ACCT-100','Aster Field Systems','ACTIVE'),"
            "('LEG-ACCT-200','Boreal Equipment Group','ACTIVE'),"
            "('LEG-ACCT-300','Available enterprise billing account','ACTIVE'),"
            "('LEG-ACCT-301','Available enterprise billing account','ACTIVE'),"
            "('LEG-ACCT-302','Available enterprise billing account','ACTIVE')",
        ]
        for sequence in range(1, 1001):
            statements.append(
                "INSERT INTO IOTCONNECT_POC.IOT.SIM_INVENTORY VALUES ("
                + values(
                    seeded_uuid("sim", f"SIM-{sequence:06d}"),
                    f"8901410321111851{sequence:04d}",
                    f"3101501234{sequence:05d}",
                    "OPERATOR",
                    "RVS",
                    "AVAILABLE",
                )
                + f", {now})"
            )
            statements.append(
                "INSERT INTO IOTCONNECT_POC.IOT.MDN_INVENTORY VALUES ("
                + values(
                    seeded_uuid("mdn", f"MDN-{sequence:06d}"),
                    f"312555{sequence:04d}",
                    sequence,
                    "AVAILABLE",
                    None,
                )
                + f", {now})"
            )
        self.client.execute_many(statements)
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
        rows = self.client.execute(f"SELECT {sequence}.NEXTVAL AS VALUE")
        return int(rows[0]["value"])

    def list_accounts(self) -> list[dict]:
        return self.client.execute(
            "SELECT ACCOUNT_ID,ACCOUNT_NUMBER,ACCOUNT_NAME,CUSTOMER_ID,CONTRACT_ID,EXTERNAL_CUSTOMER_REF,"
            "EXTERNAL_BILLING_ACCOUNT_NUMBER,SEND_SUBSCRIPTIONS_TO_AMDOCS,PRIVATE_APN_NAME,BILLING_MODE,STATUS,UPDATED_BY,"
            "TO_VARCHAR(UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT "
            "FROM IOTCONNECT_POC.IOT.ACCOUNTS ORDER BY ACCOUNT_NUMBER"
        )

    def get_account(self, account_id: str) -> dict | None:
        rows = self.client.execute(
            "SELECT ACCOUNT_ID,ACCOUNT_NUMBER,ACCOUNT_NAME,CUSTOMER_ID,CONTRACT_ID,EXTERNAL_CUSTOMER_REF,"
            "EXTERNAL_BILLING_ACCOUNT_NUMBER,SEND_SUBSCRIPTIONS_TO_AMDOCS,PRIVATE_APN_NAME,BILLING_MODE,STATUS,UPDATED_BY,"
            "TO_VARCHAR(UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT "
            f"FROM IOTCONNECT_POC.IOT.ACCOUNTS WHERE ACCOUNT_ID={sql_value(account_id)}"
        )
        return rows[0] if rows else None

    def insert_account(self, row: dict) -> None:
        if self.get_account(row["account_id"]):
            raise ConflictError("account_id already exists")
        self._write(
            "INSERT INTO IOTCONNECT_POC.IOT.ACCOUNTS "
            "(ACCOUNT_ID,ACCOUNT_NUMBER,ACCOUNT_NAME,CUSTOMER_ID,CONTRACT_ID,EXTERNAL_CUSTOMER_REF,EXTERNAL_BILLING_ACCOUNT_NUMBER,SEND_SUBSCRIPTIONS_TO_AMDOCS,PRIVATE_APN_NAME,BILLING_MODE,STATUS,UPDATED_BY,UPDATED_AT) VALUES ("
            + values(
                row["account_id"], row["account_number"], row["account_name"],
                row["customer_id"], row["contract_id"], row["external_customer_ref"],
                row["external_billing_account_number"], row["send_subscriptions_to_amdocs"],
                row.get("private_apn_name"), row["billing_mode"], row["status"], row["updated_by"],
            )
            + f", {sql_value(row['updated_at'])}::TIMESTAMP_TZ)"
        )

    def update_account(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.IOT.ACCOUNTS SET "
            f"ACCOUNT_NAME={sql_value(row['account_name'])},EXTERNAL_CUSTOMER_REF={sql_value(row['external_customer_ref'])},"
            f"EXTERNAL_BILLING_ACCOUNT_NUMBER={sql_value(row['external_billing_account_number'])},"
            f"SEND_SUBSCRIPTIONS_TO_AMDOCS={sql_value(row['send_subscriptions_to_amdocs'])},"
            f"PRIVATE_APN_NAME={sql_value(row.get('private_apn_name'))},"
            f"BILLING_MODE={sql_value(row['billing_mode'])},"
            f"STATUS={sql_value(row['status'])},UPDATED_BY={sql_value(row['updated_by'])},"
            f"UPDATED_AT={sql_value(row['updated_at'])}::TIMESTAMP_TZ WHERE ACCOUNT_ID={sql_value(row['account_id'])}"
        )

    def insert_customer(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.IOT.CUSTOMERS VALUES ("
            + values(
                row["customer_id"], row["customer_number"], row["customer_name"],
                row["status"],
            )
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ)"
        )

    def get_customer(self, customer_id: str) -> dict | None:
        rows = self.client.execute(
            "SELECT CUSTOMER_ID,CUSTOMER_NUMBER,CUSTOMER_NAME,STATUS,"
            "TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT "
            "FROM IOTCONNECT_POC.IOT.CUSTOMERS WHERE CUSTOMER_ID="
            + sql_value(customer_id)
        )
        return rows[0] if rows else None

    def update_customer(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.IOT.CUSTOMERS SET "
            f"CUSTOMER_NAME={sql_value(row['customer_name'])},STATUS={sql_value(row['status'])} "
            f"WHERE CUSTOMER_ID={sql_value(row['customer_id'])}"
        )

    def get_contract(self, contract_id: str) -> dict | None:
        rows = self.client.execute(
            "SELECT CONTRACT_ID,CONTRACT_NUMBER,ACCOUNT_ID,CONTRACT_NAME,STATUS,"
            "TO_VARCHAR(EFFECTIVE_DATE,'YYYY-MM-DD') EFFECTIVE_DATE FROM IOTCONNECT_POC.IOT.CONTRACTS "
            f"WHERE CONTRACT_ID={sql_value(contract_id)}"
        )
        return rows[0] if rows else None

    def insert_contract(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.IOT.CONTRACTS VALUES ("
            + values(row["contract_id"], row["contract_number"], row["account_id"], row["contract_name"], row["status"])
            + f", {sql_value(row['effective_date'])}::DATE)"
        )

    def insert_legacy_account(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.LEGACY.ACCOUNTS VALUES ("
            + values(row["legacy_account_ref"], row["account_name"], row["status"])
            + ")"
        )

    def get_legacy_account(self, legacy_account_ref: str) -> dict | None:
        rows = self.client.execute(
            "SELECT LEGACY_ACCOUNT_REF,ACCOUNT_NAME,STATUS FROM IOTCONNECT_POC.LEGACY.ACCOUNTS "
            f"WHERE LEGACY_ACCOUNT_REF={sql_value(legacy_account_ref)}"
        )
        return rows[0] if rows else None

    def list_legacy_accounts(self) -> list[dict]:
        return self.client.execute(
            "SELECT LEGACY_ACCOUNT_REF,ACCOUNT_NAME,STATUS,"
            "TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT "
            "FROM IOTCONNECT_POC.LEGACY.ACCOUNTS ORDER BY LEGACY_ACCOUNT_REF"
        )

    def insert_legacy_line(self, row: dict) -> None:
        if self.get_legacy_line(row["legacy_line_ref"]):
            raise ConflictError(f"legacy_line_ref {row['legacy_line_ref']} already exists")
        self._write(
            "INSERT INTO IOTCONNECT_POC.LEGACY.LINES "
            "(LEGACY_LINE_ID,LEGACY_LINE_REF,LEGACY_ACCOUNT_REF,SOURCE_SUBSCRIPTION_ID,MDN,LINE_TYPE,STATUS,CREATED_AT) VALUES ("
            + values(
                row["legacy_line_id"], row["legacy_line_ref"], row["legacy_account_ref"],
                row["source_subscription_id"], row.get("mdn"), row["line_type"], row["status"],
            )
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ)"
        )

    def get_legacy_line(self, legacy_line_ref: str) -> dict | None:
        rows = self.client.execute(
            "SELECT LEGACY_LINE_ID,LEGACY_LINE_REF,LEGACY_ACCOUNT_REF,SOURCE_SUBSCRIPTION_ID,MDN,LINE_TYPE,STATUS,"
            "TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT "
            f"FROM IOTCONNECT_POC.LEGACY.LINES WHERE LEGACY_LINE_REF={sql_value(legacy_line_ref)}"
        )
        return rows[0] if rows else None

    def list_legacy_lines(self, legacy_account_ref: str) -> list[dict]:
        return self.client.execute(
            "SELECT LEGACY_LINE_ID,LEGACY_LINE_REF,LEGACY_ACCOUNT_REF,SOURCE_SUBSCRIPTION_ID,MDN,LINE_TYPE,STATUS,"
            "TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT "
            f"FROM IOTCONNECT_POC.LEGACY.LINES WHERE LEGACY_ACCOUNT_REF={sql_value(legacy_account_ref)} ORDER BY LEGACY_LINE_REF"
        )

    def find_subscription_by_source(self, account_id: str, source_subscription_ref: str) -> dict | None:
        rows = self.client.execute(
            self._subscription_select()
            + f"FROM IOTCONNECT_POC.IOT.SUBSCRIPTIONS S WHERE S.ACCOUNT_ID={sql_value(account_id)} AND S.SOURCE_SUBSCRIPTION_REF={sql_value(source_subscription_ref)}"
        )
        return rows[0] if rows else None

    def find_subscription_by_sim(self, sim_id: str) -> dict | None:
        rows = self.client.execute(
            self._subscription_select()
            + "FROM IOTCONNECT_POC.IOT.SUBSCRIPTIONS S JOIN IOTCONNECT_POC.IOT.SUBSCRIPTION_RESOURCES R "
            "ON S.SUBSCRIPTION_ID=R.SUBSCRIPTION_ID "
            f"WHERE R.RESOURCE_TYPE='SIM' AND R.RESOURCE_ID={sql_value(sim_id)}"
        )
        return rows[0] if rows else None

    def insert_subscription(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.IOT.SUBSCRIPTIONS VALUES ("
            + values(
                row["subscription_id"], row["subscription_number"], row["source_subscription_ref"],
                row["account_id"], row["account_number"], row["contract_id"],
                row["product_offering_id"], row["price_plan_id"],
                row["technical_profile_id"], row["status"],
            )
            + f", {sql_value(row['start_date'])}::DATE, {sql_value(row['end_date'])}::DATE,"
            + f" {sql_value(row['activated_at'])}::TIMESTAMP_TZ, "
            + values(row["source_batch_id"], row["source_batch_number"])
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ, {sql_value(row['updated_at'])}::TIMESTAMP_TZ"
            + ")"
        )

    def update_subscription(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.IOT.SUBSCRIPTIONS SET "
            f"STATUS={sql_value(row['status'])},END_DATE={sql_value(row['end_date'])}::DATE,"
            f"ACTIVATED_AT={sql_value(row['activated_at'])}::TIMESTAMP_TZ,"
            f"UPDATED_AT={sql_value(row['updated_at'])}::TIMESTAMP_TZ "
            f"WHERE SUBSCRIPTION_ID={sql_value(row['subscription_id'])}"
        )

    def list_subscriptions(self, account_id: str) -> list[dict]:
        return self.client.execute(
            self._subscription_select()
            + f"FROM IOTCONNECT_POC.IOT.SUBSCRIPTIONS S WHERE S.ACCOUNT_ID={sql_value(account_id)} ORDER BY S.SUBSCRIPTION_NUMBER"
        )

    @staticmethod
    def _subscription_select() -> str:
        return (
            "SELECT S.SUBSCRIPTION_ID,S.SUBSCRIPTION_NUMBER,S.SOURCE_SUBSCRIPTION_REF,S.ACCOUNT_ID,"
            "S.ACCOUNT_NUMBER,S.CONTRACT_ID,S.PRODUCT_OFFERING_ID,S.PRICE_PLAN_ID,"
            "S.TECHNICAL_PROFILE_ID,S.STATUS,TO_VARCHAR(S.START_DATE,'YYYY-MM-DD') START_DATE,"
            "TO_VARCHAR(S.END_DATE,'YYYY-MM-DD') END_DATE,"
            "TO_VARCHAR(S.ACTIVATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') ACTIVATED_AT,"
            "S.SOURCE_BATCH_ID,S.SOURCE_BATCH_NUMBER,"
            "TO_VARCHAR(S.CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT,"
            "TO_VARCHAR(S.UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT "
        )

    def list_sims(self, account_id: str | None = None) -> list[dict]:
        predicate = ""
        if account_id is not None:
            predicate = (
                " WHERE CURRENT_OWNER_TYPE='ACCOUNT' AND CURRENT_OWNER_REF="
                + sql_value(account_id)
            )
        return self.client.execute(
            "SELECT SIM_RESOURCE_ID,ICCID,IMSI,CURRENT_OWNER_TYPE,CURRENT_OWNER_REF,RESOURCE_STATUS,"
            "TO_VARCHAR(UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT "
            "FROM IOTCONNECT_POC.IOT.SIM_INVENTORY"
            + predicate
            + " ORDER BY ICCID"
        )

    def list_account_resource_views(self, account_id: str) -> list[dict]:
        account = sql_value(account_id)
        return self.client.execute(
            "SELECT SIM.SIM_RESOURCE_ID,SIM.ICCID,SIM.IMSI,SIM.CURRENT_OWNER_TYPE,"
            "SIM.CURRENT_OWNER_REF,SIM.RESOURCE_STATUS,"
            "TO_VARCHAR(SIM.UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT,"
            "SUB.SUBSCRIPTION_ID,SUB.SUBSCRIPTION_NUMBER,SUB.PRICE_PLAN_ID,"
            "SUB.STATUS SUBSCRIPTION_STATUS,"
            "TO_VARCHAR(SUB.UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') SUBSCRIPTION_UPDATED_AT,"
            "MDN.MDN_RESOURCE_ID,MDN.MDN "
            "FROM IOTCONNECT_POC.IOT.SIM_INVENTORY SIM "
            "LEFT JOIN IOTCONNECT_POC.IOT.SUBSCRIPTION_RESOURCES SIM_LINK "
            "ON SIM_LINK.RESOURCE_TYPE='SIM' AND SIM_LINK.RESOURCE_ID=SIM.SIM_RESOURCE_ID "
            "AND SIM_LINK.STATUS='ACTIVE' "
            "LEFT JOIN IOTCONNECT_POC.IOT.SUBSCRIPTIONS SUB "
            f"ON SUB.SUBSCRIPTION_ID=SIM_LINK.SUBSCRIPTION_ID AND SUB.ACCOUNT_ID={account} "
            "LEFT JOIN IOTCONNECT_POC.IOT.SUBSCRIPTION_RESOURCES MDN_LINK "
            "ON MDN_LINK.SUBSCRIPTION_ID=SUB.SUBSCRIPTION_ID "
            "AND MDN_LINK.RESOURCE_TYPE='MDN' AND MDN_LINK.STATUS='ACTIVE' "
            "LEFT JOIN IOTCONNECT_POC.IOT.MDN_INVENTORY MDN "
            "ON MDN.MDN_RESOURCE_ID=MDN_LINK.RESOURCE_ID "
            f"WHERE SIM.CURRENT_OWNER_TYPE='ACCOUNT' AND SIM.CURRENT_OWNER_REF={account} "
            "ORDER BY SIM.ICCID"
        )

    def get_sim(self, sim_resource_id: str) -> dict | None:
        rows = self.client.execute(
            "SELECT SIM_RESOURCE_ID,ICCID,IMSI,CURRENT_OWNER_TYPE,CURRENT_OWNER_REF,RESOURCE_STATUS,"
            "TO_VARCHAR(UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT "
            "FROM IOTCONNECT_POC.IOT.SIM_INVENTORY WHERE SIM_RESOURCE_ID="
            + sql_value(sim_resource_id)
        )
        return rows[0] if rows else None

    def insert_sim(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.IOT.SIM_INVENTORY VALUES ("
            + values(
                row["sim_resource_id"], row["iccid"], row["imsi"],
                row["current_owner_type"], row["current_owner_ref"],
                row["resource_status"],
            )
            + f", {sql_value(row['updated_at'])}::TIMESTAMP_TZ)"
        )

    def update_sim(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.IOT.SIM_INVENTORY SET "
            f"CURRENT_OWNER_TYPE={sql_value(row['current_owner_type'])},"
            f"CURRENT_OWNER_REF={sql_value(row['current_owner_ref'])},"
            f"RESOURCE_STATUS={sql_value(row['resource_status'])},"
            f"UPDATED_AT={sql_value(row['updated_at'])}::TIMESTAMP_TZ "
            f"WHERE SIM_RESOURCE_ID={sql_value(row['sim_resource_id'])}"
        )

    def list_mdns(self, status: str | None = None) -> list[dict]:
        predicate = f" WHERE STATUS={sql_value(status)}" if status else ""
        return self.client.execute(
            "SELECT MDN_RESOURCE_ID,MDN,ALLOCATION_SEQUENCE,STATUS,ASSIGNED_ACCOUNT_ID,"
            "TO_VARCHAR(UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT "
            "FROM IOTCONNECT_POC.IOT.MDN_INVENTORY"
            + predicate
            + " ORDER BY ALLOCATION_SEQUENCE"
        )

    def get_mdn(self, mdn_resource_id: str) -> dict | None:
        rows = self.client.execute(
            "SELECT MDN_RESOURCE_ID,MDN,ALLOCATION_SEQUENCE,STATUS,ASSIGNED_ACCOUNT_ID,"
            "TO_VARCHAR(UPDATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') UPDATED_AT "
            "FROM IOTCONNECT_POC.IOT.MDN_INVENTORY WHERE MDN_RESOURCE_ID="
            + sql_value(mdn_resource_id)
        )
        return rows[0] if rows else None

    def update_mdn(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.IOT.MDN_INVENTORY SET "
            f"STATUS={sql_value(row['status'])},ASSIGNED_ACCOUNT_ID={sql_value(row['assigned_account_id'])},"
            f"UPDATED_AT={sql_value(row['updated_at'])}::TIMESTAMP_TZ "
            f"WHERE MDN_RESOURCE_ID={sql_value(row['mdn_resource_id'])}"
        )

    def insert_subscription_resource(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.IOT.SUBSCRIPTION_RESOURCES VALUES ("
            + values(
                row["subscription_resource_id"], row["subscription_id"],
                row["resource_type"], row["resource_id"], row["resource_role"],
                row["status"],
            )
            + f", {sql_value(row['effective_from'])}::TIMESTAMP_TZ,"
            + f" {sql_value(row['effective_to'])}::TIMESTAMP_TZ)"
        )

    def update_subscription_resource(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.IOT.SUBSCRIPTION_RESOURCES SET "
            f"STATUS={sql_value(row['status'])},EFFECTIVE_TO={sql_value(row['effective_to'])}::TIMESTAMP_TZ "
            f"WHERE SUBSCRIPTION_RESOURCE_ID={sql_value(row['subscription_resource_id'])}"
        )

    def list_subscription_resources(self, subscription_id: str) -> list[dict]:
        return self.client.execute(
            "SELECT SUBSCRIPTION_RESOURCE_ID,SUBSCRIPTION_ID,RESOURCE_TYPE,RESOURCE_ID,RESOURCE_ROLE,STATUS,"
            "TO_VARCHAR(EFFECTIVE_FROM,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') EFFECTIVE_FROM,"
            "TO_VARCHAR(EFFECTIVE_TO,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') EFFECTIVE_TO "
            "FROM IOTCONNECT_POC.IOT.SUBSCRIPTION_RESOURCES WHERE SUBSCRIPTION_ID="
            + sql_value(subscription_id)
        )

    def insert_activation_batch(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.CONTROL.ACTIVATION_BATCHES VALUES ("
            + values(
                row["batch_id"], row["batch_number"], row["account_id"],
                row["status"], row["item_count"], row["success_count"],
                row["failure_count"], row["actor"],
            )
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ,"
            + f" {sql_value(row['submitted_at'])}::TIMESTAMP_TZ,"
            + f" {sql_value(row['completed_at'])}::TIMESTAMP_TZ)"
        )

    def update_activation_batch(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.CONTROL.ACTIVATION_BATCHES SET "
            f"STATUS={sql_value(row['status'])},SUCCESS_COUNT={sql_value(row['success_count'])},"
            f"FAILURE_COUNT={sql_value(row['failure_count'])},"
            f"SUBMITTED_AT={sql_value(row['submitted_at'])}::TIMESTAMP_TZ,"
            f"COMPLETED_AT={sql_value(row['completed_at'])}::TIMESTAMP_TZ "
            f"WHERE BATCH_ID={sql_value(row['batch_id'])}"
        )

    def get_activation_batch(self, batch_id: str) -> dict | None:
        rows = self.client.execute(
            "SELECT BATCH_ID,BATCH_NUMBER,ACCOUNT_ID,STATUS,ITEM_COUNT,SUCCESS_COUNT,FAILURE_COUNT,ACTOR,"
            "TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT,"
            "TO_VARCHAR(SUBMITTED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') SUBMITTED_AT,"
            "TO_VARCHAR(COMPLETED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') COMPLETED_AT "
            "FROM IOTCONNECT_POC.CONTROL.ACTIVATION_BATCHES WHERE BATCH_ID="
            + sql_value(batch_id)
        )
        return rows[0] if rows else None

    def list_activation_batches(self, account_id: str | None = None) -> list[dict]:
        where = "" if account_id is None else " WHERE ACCOUNT_ID=" + sql_value(account_id)
        return self.client.execute(
            "SELECT BATCH_ID,BATCH_NUMBER,ACCOUNT_ID,STATUS,ITEM_COUNT,SUCCESS_COUNT,FAILURE_COUNT,ACTOR,"
            "TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT,"
            "TO_VARCHAR(SUBMITTED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') SUBMITTED_AT,"
            "TO_VARCHAR(COMPLETED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') COMPLETED_AT "
            "FROM IOTCONNECT_POC.CONTROL.ACTIVATION_BATCHES"
            + where
            + " ORDER BY CREATED_AT DESC"
        )

    def insert_activation_batch_item(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.CONTROL.ACTIVATION_BATCH_ITEMS "
            "(BATCH_ITEM_ID,BATCH_ID,ITEM_NUMBER,SOURCE_ORDER_REF,SUBSCRIPTION_ID,SIM_RESOURCE_ID,MDN_RESOURCE_ID,PRIVATE_APN,NETWORK_STATUS,FLOWONE_ACTIVATION_ID,LEGACY_STATUS,LEGACY_ACTION_ID,OVERALL_STATUS,MESSAGE,CREATED_AT,COMPLETED_AT) VALUES ("
            + values(
                row["batch_item_id"], row["batch_id"], row["item_number"],
                row["source_order_ref"], row["subscription_id"],
                row["sim_resource_id"], row["mdn_resource_id"],
                row.get("private_apn"),
                row["network_status"], row["flowone_activation_id"],
                row["legacy_status"], row["legacy_action_id"], row["overall_status"],
                row["message"],
            )
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ,"
            + f" {sql_value(row['completed_at'])}::TIMESTAMP_TZ)"
        )

    def update_activation_batch_item(self, row: dict) -> None:
        self._write(
            "UPDATE IOTCONNECT_POC.CONTROL.ACTIVATION_BATCH_ITEMS SET "
            f"NETWORK_STATUS={sql_value(row['network_status'])},"
            f"FLOWONE_ACTIVATION_ID={sql_value(row['flowone_activation_id'])},"
            f"LEGACY_STATUS={sql_value(row['legacy_status'])},"
            f"LEGACY_ACTION_ID={sql_value(row['legacy_action_id'])},"
            f"OVERALL_STATUS={sql_value(row['overall_status'])},MESSAGE={sql_value(row['message'])},"
            f"COMPLETED_AT={sql_value(row['completed_at'])}::TIMESTAMP_TZ "
            f"WHERE BATCH_ITEM_ID={sql_value(row['batch_item_id'])}"
        )

    def list_activation_batch_items(self, batch_id: str) -> list[dict]:
        return self.client.execute(
            "SELECT BATCH_ITEM_ID,BATCH_ID,ITEM_NUMBER,SOURCE_ORDER_REF,SUBSCRIPTION_ID,SIM_RESOURCE_ID,"
            "MDN_RESOURCE_ID,PRIVATE_APN,NETWORK_STATUS,FLOWONE_ACTIVATION_ID,LEGACY_STATUS,LEGACY_ACTION_ID,"
            "OVERALL_STATUS,MESSAGE,TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT,"
            "TO_VARCHAR(COMPLETED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') COMPLETED_AT "
            "FROM IOTCONNECT_POC.CONTROL.ACTIVATION_BATCH_ITEMS WHERE BATCH_ID="
            + sql_value(batch_id)
            + " ORDER BY ITEM_NUMBER"
        )

    def insert_flowone_element_result(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.CONTROL.FLOWONE_ELEMENT_RESULTS VALUES ("
            + values(
                row["element_result_id"], row["batch_item_id"], row["sequence_number"],
                row["element"], row["operation"], row["provisioning_status"],
                row["element_code"], row["message"], row["rollback_status"],
                row["applied_profile"],
            )
            + f", {sql_value(row['recorded_at'])}::TIMESTAMP_TZ)"
        )

    def list_flowone_element_results(self, batch_item_id: str) -> list[dict]:
        return self.client.execute(
            "SELECT ELEMENT_RESULT_ID,BATCH_ITEM_ID,SEQUENCE_NUMBER,ELEMENT,OPERATION,PROVISIONING_STATUS,"
            "ELEMENT_CODE,MESSAGE,ROLLBACK_STATUS,APPLIED_PROFILE,"
            "TO_VARCHAR(RECORDED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') RECORDED_AT "
            "FROM IOTCONNECT_POC.CONTROL.FLOWONE_ELEMENT_RESULTS WHERE BATCH_ITEM_ID="
            + sql_value(batch_item_id)
            + " ORDER BY SEQUENCE_NUMBER"
        )

    _DIRECT_ACTIONS_UNSUPPORTED = (
        "The Snowflake adapter does not persist direct integration-action resources "
        "(network activations / legacy subscription actions). Use the PostgreSQL or "
        "memory repository for the standalone beta."
    )

    def insert_network_activation(self, row: dict) -> None:
        raise NotImplementedError(self._DIRECT_ACTIONS_UNSUPPORTED)

    def get_network_activation(self, activation_id: str) -> dict | None:
        raise NotImplementedError(self._DIRECT_ACTIONS_UNSUPPORTED)

    def insert_legacy_subscription_action(self, row: dict) -> None:
        raise NotImplementedError(self._DIRECT_ACTIONS_UNSUPPORTED)

    def get_legacy_subscription_action(self, compatibility_action_id: str) -> dict | None:
        raise NotImplementedError(self._DIRECT_ACTIONS_UNSUPPORTED)

    def insert_audit_event(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.CONTROL.AUDIT_EVENTS VALUES ("
            + values(row["audit_id"], row["account_id"], row["event_type"], row["actor"], row["reason"], row["details"])
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ)"
        )

    def insert_activation_event(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.CONTROL.ACTIVATION_EVENTS VALUES ("
            + values(
                row["event_id"], row["batch_id"], row["batch_number"], row["account_id"],
                row["contract_id"], row["subscription_id"], row["source_subscription_ref"],
                row["iot_outcome"], row["legacy_outcome"], row["actor"],
            )
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ)"
        )

    def delete_billing_for_account_cycle(self, account_id: str, bill_cycle: str) -> None:
        predicate = f"ACCOUNT_ID={sql_value(account_id)} AND BILL_CYCLE={sql_value(bill_cycle)}"
        run_ids = f"SELECT BILL_RUN_ID FROM IOTCONNECT_POC.CONTROL.BILL_RUNS WHERE {predicate}"
        self._write(f"DELETE FROM IOTCONNECT_POC.IOT.CHARGES WHERE BILL_RUN_ID IN ({run_ids})")
        self._write(f"DELETE FROM IOTCONNECT_POC.LEGACY.BILLING_ROWS WHERE BILL_RUN_ID IN ({run_ids})")
        self._write(f"DELETE FROM IOTCONNECT_POC.CONTROL.BILL_RUNS WHERE {predicate}")

    def insert_charge(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.IOT.CHARGES "
            "(CHARGE_ID,BILL_RUN_ID,ACCOUNT_ID,CONTRACT_ID,SUBSCRIPTION_ID,SUBSCRIPTION_NUMBER,BILL_CYCLE,CHARGE_LEVEL,CHARGE_CODE,RATE_PLAN_ID,RATE_PLAN_CODE,DESCRIPTION,CHARGE_TYPE,QUANTITY,UNIT_PRICE,AMOUNT,GL_CODE,CURRENCY) VALUES ("
            + values(
                row["charge_id"], row["bill_run_id"], row["account_id"], row["contract_id"],
                row["subscription_id"], row["subscription_number"], row["bill_cycle"],
                row["charge_level"], row["charge_code"], row["rate_plan_id"], row["rate_plan_code"], row["description"],
                row["charge_type"], row["quantity"], row["unit_price"], row["amount"],
                row["gl_code"], row["currency"],
            )
            + ")"
        )

    def insert_billing_row(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.LEGACY.BILLING_ROWS "
            "(BILLING_ROW_ID,BILL_RUN_ID,ROW_NUMBER,BILL_CYCLE,ACCOUNT_ID,ACCOUNT_NUMBER,CONTRACT_ID,LEGACY_ACCOUNT_REF,TARGET_LINE_REF,SOURCE_CHARGE_LEVEL,POSTING_SCOPE,MDN,CHARGE_CODE,RATE_PLAN_ID,RATE_PLAN_CODE,DESCRIPTION,CHARGE_TYPE,QUANTITY,UNIT_PRICE,AMOUNT,GL_CODE,CURRENCY,SOURCE_RECORD_COUNT,SOURCE_CHARGE_IDS) VALUES ("
            + values(
                row["billing_row_id"], row["bill_run_id"], row["row_number"], row["bill_cycle"],
                row["account_id"], row["account_number"], row["contract_id"], row["legacy_account_ref"],
                row["target_line_ref"], row["source_charge_level"], row["posting_scope"], row["mdn"],
                row["charge_code"], row["rate_plan_id"], row["rate_plan_code"],
                row["description"], row["charge_type"], row["quantity"], row["unit_price"],
                row["amount"], row["gl_code"], row["currency"], row["source_record_count"],
                row["source_charge_ids"],
            )
            + ")"
        )

    def insert_bill_run(self, row: dict) -> None:
        self._write(
            "INSERT INTO IOTCONNECT_POC.CONTROL.BILL_RUNS VALUES ("
            + values(
                row["bill_run_id"], row["bill_run_number"], row["account_id"], row["account_number"],
                row["contract_id"], row["account_name"], row["billing_mode"], row["bill_cycle"],
                row["status"], row["source_charge_count"], row["output_row_count"], row["source_total"],
                row["output_total"], row["variance"], row["unrepresented_source_records"],
                row["duplicate_source_representations"], row["invalid_target_lines"], row["actor"],
            )
            + f", {sql_value(row['created_at'])}::TIMESTAMP_TZ)"
        )

    def get_bill_run(self, bill_run_id: str) -> dict | None:
        rows = self._bill_run_query(f"BILL_RUN_ID={sql_value(bill_run_id)}")
        return rows[0] if rows else None

    def list_bill_runs(self, account_id: str | None = None) -> list[dict]:
        predicate = "1=1" if account_id is None else f"ACCOUNT_ID={sql_value(account_id)}"
        return self._bill_run_query(predicate)

    def _bill_run_query(self, predicate: str) -> list[dict]:
        return self.client.execute(
            "SELECT BILL_RUN_ID,BILL_RUN_NUMBER,ACCOUNT_ID,ACCOUNT_NUMBER,CONTRACT_ID,ACCOUNT_NAME,BILLING_MODE,BILL_CYCLE,STATUS,"
            "SOURCE_CHARGE_COUNT,OUTPUT_ROW_COUNT,SOURCE_TOTAL,OUTPUT_TOTAL,VARIANCE,UNREPRESENTED_SOURCE_RECORDS,"
            "DUPLICATE_SOURCE_REPRESENTATIONS,INVALID_TARGET_LINES,ACTOR,"
            "TO_VARCHAR(CREATED_AT,'YYYY-MM-DDTHH24:MI:SS.FF3TZH:TZM') CREATED_AT "
            f"FROM IOTCONNECT_POC.CONTROL.BILL_RUNS WHERE {predicate} ORDER BY CREATED_AT DESC"
        )

    def list_charges(self, bill_run_id: str) -> list[dict]:
        return self.client.execute(
            "SELECT CHARGE_ID,BILL_RUN_ID,ACCOUNT_ID,CONTRACT_ID,SUBSCRIPTION_ID,SUBSCRIPTION_NUMBER,BILL_CYCLE,CHARGE_LEVEL,"
            "CHARGE_CODE,RATE_PLAN_ID,RATE_PLAN_CODE,DESCRIPTION,CHARGE_TYPE,QUANTITY,UNIT_PRICE,AMOUNT,GL_CODE,CURRENCY "
            f"FROM IOTCONNECT_POC.IOT.CHARGES WHERE BILL_RUN_ID={sql_value(bill_run_id)} ORDER BY CHARGE_LEVEL,CHARGE_ID"
        )

    def list_billing_rows(self, bill_run_id: str) -> list[dict]:
        return self.client.execute(
            "SELECT BILLING_ROW_ID,BILL_RUN_ID,ROW_NUMBER,BILL_CYCLE,ACCOUNT_ID,ACCOUNT_NUMBER,CONTRACT_ID,LEGACY_ACCOUNT_REF,"
            "TARGET_LINE_REF,SOURCE_CHARGE_LEVEL,POSTING_SCOPE,MDN,CHARGE_CODE,RATE_PLAN_ID,RATE_PLAN_CODE,DESCRIPTION,CHARGE_TYPE,QUANTITY,UNIT_PRICE,AMOUNT,GL_CODE,"
            "CURRENCY,SOURCE_RECORD_COUNT,SOURCE_CHARGE_IDS FROM IOTCONNECT_POC.LEGACY.BILLING_ROWS "
            f"WHERE BILL_RUN_ID={sql_value(bill_run_id)} ORDER BY ROW_NUMBER"
        )

    def latest_bill_run(self, account_id: str, bill_cycle: str) -> dict | None:
        rows = self._bill_run_query(
            f"ACCOUNT_ID={sql_value(account_id)} AND BILL_CYCLE={sql_value(bill_cycle)}"
        )
        return rows[0] if rows else None
