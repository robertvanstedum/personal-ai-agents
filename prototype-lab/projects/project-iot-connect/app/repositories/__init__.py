"""Persistence adapters implementing the same repository contract."""

# Repository selection policy for IoT Connect v0.9 (decision 2026-09-03):
# PostgreSQL operates the demo, memory serves tests/fallback, anything else
# (including the retained Snowflake adapter source) is rejected at startup.
SUPPORTED_STORES = ("postgres", "memory")
UNSUPPORTED_STORE_MESSAGE = (
    "IOTCONNECT_STORE={value} is not supported in IoT Connect v0.9; use postgres "
    "(operating) or memory (tests). Snowflake may return later as a downstream "
    "reporting destination, not in the activation path."
)
