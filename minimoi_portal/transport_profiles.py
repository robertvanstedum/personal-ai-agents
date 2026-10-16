"""Named server-owned transport profiles. Selection never comes from a request."""
from dataclasses import dataclass
@dataclass(frozen=True)
class RecordsProfile:
    name: str
    origin: str
    host: str
    assertion_env: str
    backend: str | None = None
DEV = RecordsProfile("dev", "https://dev.minimoi.ai", "dev.minimoi.ai", "dev")
PRODUCTION = RecordsProfile("production", "https://minimoi.ai", "minimoi.ai", "production", "http://minimoi-records:18880")
PRODUCTION_FLAG = "MINIMOI_RECORDS_PRODUCTION"
PROFILE_ENV = "MINIMOI_RECORDS_PROFILE"
def select_records_profile(base_url, environ):
    selected = environ.get(PROFILE_ENV, "dev")
    if (base_url == PRODUCTION.origin and selected == "production"
            and environ.get("MINIMOI_ROLE") == "production"
            and environ.get(PRODUCTION_FLAG) == "1"):
        return PRODUCTION
    if selected == "dev" and environ.get("MINIMOI_ROLE") != "production" and (base_url or "").rstrip("/") == DEV.origin:
        return DEV
    return None
