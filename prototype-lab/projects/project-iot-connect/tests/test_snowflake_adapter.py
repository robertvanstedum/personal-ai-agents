import pytest
from app.repositories.snowflake import SnowflakeRepository, SnowflakeSqlApi, sql_value


class RecordingClient:
    def __init__(self):
        self.single = []
        self.batches = []

    def execute(self, statement):
        self.single.append(statement)
        return []

    def execute_many(self, statements):
        self.batches.append(list(statements))


def test_sql_values_escape_strings_and_encode_json():
    assert sql_value("O'Brien's account") == "'O''Brien''s account'"
    assert sql_value(None) == "NULL"
    assert sql_value(True) == "TRUE"
    assert sql_value(["a", "b"]) == "PARSE_JSON('[\"a\",\"b\"]')"


def test_jsonv2_type_conversion_happens_at_adapter_boundary():
    result = {
        "resultSetMetaData": {
            "rowType": [
                {"name": "ROW_COUNT", "type": "fixed", "scale": 0},
                {"name": "AMOUNT", "type": "fixed", "scale": 2},
                {"name": "ACTIVE", "type": "boolean"},
                {"name": "SOURCE_IDS", "type": "array"},
            ]
        },
        "data": [["4", "65.00", "true", '["one","two"]']],
    }
    assert SnowflakeSqlApi._rows(result) == [
        {
            "row_count": 4,
            "amount": "65.00",
            "active": True,
            "source_ids": ["one", "two"],
        }
    ]


def test_snowflake_writes_commit_as_one_repository_transaction():
    client = RecordingClient()
    repository = SnowflakeRepository(client)
    with repository.transaction():
        repository._write("INSERT ONE")
        repository._write("INSERT TWO")
    assert client.single == []
    assert client.batches == [["INSERT ONE", "INSERT TWO"]]


def test_failed_snowflake_transaction_does_not_submit_pending_writes():
    client = RecordingClient()
    repository = SnowflakeRepository(client)
    with pytest.raises(RuntimeError, match="fail before commit"):
        with repository.transaction():
            repository._write("INSERT ONE")
            raise RuntimeError("fail before commit")
    assert client.single == []
    assert client.batches == []
