import pytest

from conftest import BASE_DIR

SNOWFLAKE_SETUP = BASE_DIR / "snowflake" / "01_setup.sql"


@pytest.mark.skipif(
    not SNOWFLAKE_SETUP.exists(),
    reason=(
        "Snowflake is unsupported as an application database in IoT Connect v0.9 "
        "(decision 2026-09-03); the adapter source is retained but its setup SQL "
        "is not shipped"
    ),
)
def test_postgres_and_snowflake_keep_private_apn_fields_compatible():
    postgres = (BASE_DIR / "postgres" / "01_schema.sql").read_text(encoding="utf-8")
    snowflake = SNOWFLAKE_SETUP.read_text(encoding="utf-8")

    assert "private_apn_name text" in postgres.lower()
    assert "private_apn text" in postgres.lower()
    assert "PRIVATE_APN_NAME VARCHAR" in snowflake
    assert "PRIVATE_APN VARCHAR" in snowflake


def test_snowflake_adapter_maps_private_apn_without_becoming_demo_authority():
    adapter = (BASE_DIR / "app" / "repositories" / "snowflake.py").read_text(
        encoding="utf-8"
    )

    assert "PRIVATE_APN_NAME" in adapter
    assert "PRIVATE_APN" in adapter
    assert "IOTCONNECT_STORE=snowflake" not in adapter
