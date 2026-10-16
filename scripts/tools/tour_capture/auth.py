"""Portal authentication profiles for dev-only capture runs."""

from __future__ import annotations

import os
import json
import tempfile

from .paths import auth_root, ensure_private_directory, validate_private_file
from pathlib import Path


PROFILE_KEYS = {
    "owner_session": (
        "MINIMOI_CAPTURE_OWNER_USERNAME",
        "MINIMOI_CAPTURE_OWNER_PASSWORD",
    ),
}


class CaptureAuthError(RuntimeError):
    """Raised when the declared capture profile cannot authenticate."""


def _local_secret(key: str, default: str | None = None) -> str:
    """Read a capture secret from env or local Keychain, never production SSM."""
    value = os.environ.get(key)
    if value:
        return value
    try:
        import keyring

        value = keyring.get_password("minimoi-tour-capture", key.lower())
        if value:
            return value
    except Exception:
        pass
    if default is not None:
        return default
    raise CaptureAuthError(
        f"missing {key}; set it in the environment or macOS Keychain service "
        "'minimoi-tour-capture'"
    )


def credentials_for(profile: str) -> tuple[str, str]:
    try:
        username_key, password_key = PROFILE_KEYS[profile]
    except KeyError as exc:
        raise CaptureAuthError(f"unknown capture auth profile: {profile!r}") from exc
    return _local_secret(username_key, default="robert"), _local_secret(password_key)


def storage_state_path(repo_root: Path, profile: str) -> Path:
    # Keep the legacy argument for existing callers; auth is never checkout-local.
    if profile not in PROFILE_KEYS:
        raise CaptureAuthError("unknown capture authentication profile")
    configured = auth_root()
    if (any(part in {"_working", "_NewDomains"} for part in configured.parts)
            or configured.is_relative_to(repo_root.absolute())):
        raise CaptureAuthError("capture authentication state must be outside the checkout")
    root = ensure_private_directory(configured)
    path = root / f"{profile}.json"
    validate_private_file(path)
    return path


def ensure_authenticated(
    page,
    context,
    base_url: str,
    start_path: str,
    profile: str,
    state_path: Path,
) -> None:
    """Open a protected route and log in once if the stored session is stale."""
    target = f"{base_url.rstrip('/')}{start_path}"
    page.goto(target, wait_until="domcontentloaded")
    if "/login" not in page.url and page.locator("#username").count() == 0:
        return

    username, password = credentials_for(profile)
    page.goto(f"{base_url.rstrip('/')}/login", wait_until="domcontentloaded")
    page.locator("#username").fill(username)
    page.locator("#password").fill(password)
    page.locator("form button[type='submit']").click()
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=20_000)
    except Exception as exc:
        message = "portal login failed"
        error = page.locator(".login-error")
        if error.count() and error.is_visible():
            message = error.inner_text().strip() or message
        raise CaptureAuthError(message) from exc

    # Browser state contains session credentials. Atomic replacement never follows
    # an existing destination leaf; the private parent is validated again.
    ensure_private_directory(state_path.parent)
    validate_private_file(state_path)
    state = context.storage_state()
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=state_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, state_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    page.goto(target, wait_until="domcontentloaded")
    if "/login" in page.url:
        raise CaptureAuthError(f"profile {profile!r} cannot access {start_path}")
