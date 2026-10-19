"""Explicit dev-only control for chat without background jobs."""

def background_jobs_enabled(environ):
    value = environ.get("COS_BACKGROUND_JOBS", "1")
    if value == "1":
        return True
    if value != "0":
        raise RuntimeError("COS_BACKGROUND_JOBS must be 0 or 1")
    if environ.get("MINIMOI_ROLE") != "standby":
        raise RuntimeError("disabling CoS background work requires explicit standby role")
    return False
