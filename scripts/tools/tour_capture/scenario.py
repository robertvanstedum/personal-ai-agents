"""Scenario loading and validation for tour capture runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUPPORTED_ACTIONS = {
    "goto",
    "click",
    "wait_for",
    "scroll_to",
    "operator",
    "record_current_article",
    "assert_current_article",
    "screenshot",
    "free_capture",
    # Scripted local review capture (auth_profile "none" only, see LOCAL_ONLY_ACTIONS).
    "fill",
    "press",
    "evaluate",
    "route",
    "unroute",
    "sample",
}
# Actions that drive or stub the page. They are refused for owner_session so a
# scripted run can never type into, script or fake a real dev page; they are
# for loopback review captures of a local sample instance.
LOCAL_ONLY_ACTIONS = {"fill", "evaluate", "route", "unroute", "sample"}
SAMPLE_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
KEY_RE = re.compile(r"^[A-Za-z0-9+]{1,40}$")
AUTH_PROFILES = {"owner_session", "none"}
# auth_profile "none" skips login entirely; the runner only permits it against
# localhost or 127.0.0.1 so an unauthenticated run can never reach dev.
LOCAL_ONLY_AUTH_PROFILES = {"none"}


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    width: int
    height: int
    device_scale_factor: int

    @property
    def output_dimensions(self) -> tuple[int, int]:
        return (
            self.width * self.device_scale_factor,
            self.height * self.device_scale_factor,
        )


DEVICE_PROFILES = {
    "mobile": DeviceProfile("mobile", 390, 844, 3),
    "desktop": DeviceProfile("desktop", 1440, 900, 2),
}


class ScenarioValidationError(ValueError):
    """Raised when a scenario cannot be executed deterministically."""


def output_filename(
    order: int,
    domain: str,
    scene: str,
    profile: str,
    extension: str,
) -> str:
    """Return the stable tour filename declared by the specification."""
    for label, value in (("domain", domain), ("scene", scene), ("profile", profile)):
        if not SLUG_RE.fullmatch(value):
            raise ScenarioValidationError(f"invalid {label} slug: {value!r}")
    extension = extension.lower().lstrip(".")
    if extension not in {"png", "webp"}:
        raise ScenarioValidationError(f"unsupported image extension: {extension!r}")
    if order < 1 or order > 99:
        raise ScenarioValidationError("scene order must be between 1 and 99")
    return f"{order:02d}-{domain}-{scene}-{profile}.{extension}"


def _is_local_absolute_path(value: Any) -> bool:
    """Accept '/path' but never a scheme-relative '//host' or a full URL."""
    return isinstance(value, str) and value.startswith("/") and not value.startswith("//")


def _validate_viewport(value: Any) -> None:
    if not isinstance(value, dict):
        raise ScenarioValidationError("viewport must be an object")
    for key, upper in (("width", 3840), ("height", 3840), ("device_scale_factor", 4)):
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or not 1 <= item <= upper:
            raise ScenarioValidationError(
                f"viewport {key} must be an integer between 1 and {upper}"
            )


def resolve_device_profile(scenario: dict[str, Any]) -> DeviceProfile:
    """Return the scenario's viewport override, or its named built-in profile."""
    viewport = scenario.get("viewport")
    if viewport:
        return DeviceProfile(
            scenario["device_profile"],
            viewport["width"],
            viewport["height"],
            viewport["device_scale_factor"],
        )
    return DEVICE_PROFILES[scenario["device_profile"]]


def _validate_route(value: Any, index: int) -> None:
    """A stubbed answer for one URL pattern: {url, status, body?, content_type?} or {url, abort: true}."""
    if not isinstance(value, dict) or not isinstance(value.get("url"), str) or not value["url"].strip():
        raise ScenarioValidationError(f"step {index} route needs an object with a 'url' pattern")
    if value.get("abort") is True:
        if set(value) - {"url", "abort"}:
            raise ScenarioValidationError(f"step {index} route with abort takes no answer fields")
        return
    status = value.get("status")
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        raise ScenarioValidationError(f"step {index} route status must be an HTTP status code")
    body = value.get("body", "")
    if not isinstance(body, (str, dict, list)):
        raise ScenarioValidationError(f"step {index} route body must be a string or JSON value")
    if "content_type" in value and not isinstance(value["content_type"], str):
        raise ScenarioValidationError(f"step {index} route content_type must be a string")
    unknown = set(value) - {"url", "status", "body", "content_type"}
    if unknown:
        raise ScenarioValidationError(f"step {index} route has unknown keys: {sorted(unknown)}")


def _action_key(step: dict[str, Any], index: int) -> str:
    actions = [key for key in SUPPORTED_ACTIONS if key in step]
    if len(actions) != 1:
        raise ScenarioValidationError(
            f"step {index} must declare exactly one action; found {actions or 'none'}"
        )
    return actions[0]


def validate_scenario(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a scenario dictionary."""
    required = {"id", "domain", "device_profile", "auth_profile", "start_path", "steps"}
    missing = sorted(required - data.keys())
    if missing:
        raise ScenarioValidationError(f"scenario missing required keys: {', '.join(missing)}")

    for key in ("id", "domain"):
        if not isinstance(data[key], str) or not SLUG_RE.fullmatch(data[key]):
            raise ScenarioValidationError(f"scenario {key} must be a lowercase hyphenated slug")
    if "viewport" in data:
        _validate_viewport(data["viewport"])
        if not isinstance(data["device_profile"], str) or not SLUG_RE.fullmatch(
            data["device_profile"]
        ):
            raise ScenarioValidationError(
                "device_profile must be a lowercase hyphenated slug when viewport is set"
            )
    elif data["device_profile"] not in DEVICE_PROFILES:
        raise ScenarioValidationError(f"unknown device profile: {data['device_profile']!r}")
    if not isinstance(data.get("mobile_emulation", False), bool):
        raise ScenarioValidationError("mobile_emulation must be true or false")
    if data.get("color_scheme", "light") not in ("light", "dark"):
        raise ScenarioValidationError("color_scheme must be 'light' or 'dark'")
    if data["auth_profile"] not in AUTH_PROFILES:
        raise ScenarioValidationError(f"unknown auth profile: {data['auth_profile']!r}")
    unauthenticated = data["auth_profile"] in LOCAL_ONLY_AUTH_PROFILES
    if unauthenticated:
        if not _is_local_absolute_path(data["start_path"]):
            raise ScenarioValidationError(
                "start_path must be an absolute path beginning with '/'"
            )
    elif not isinstance(data["start_path"], str) or not (
        data["start_path"].startswith("/app/")
        or data["start_path"] == "/guild"
        or data["start_path"].startswith("/guild/")
    ):
        raise ScenarioValidationError(
            "start_path must be a portal-proxied /app/... path or an owner-only /guild path"
        )
    if not isinstance(data["steps"], list) or not data["steps"]:
        raise ScenarioValidationError("scenario steps must be a non-empty list")

    screenshot_names: list[str] = []
    seen_screenshot_names: set[str] = set()
    screenshot_count = 0
    operator_count = 0
    has_free_capture = False
    for index, step in enumerate(data["steps"], start=1):
        if not isinstance(step, dict):
            raise ScenarioValidationError(f"step {index} must be an object")
        action = _action_key(step, index)
        value = step[action]

        if action in {"goto", "click", "operator", "scroll_to"}:
            if not isinstance(value, str) or not value.strip():
                raise ScenarioValidationError(f"step {index} {action} must be a non-empty string")
            if action == "goto" and unauthenticated and not _is_local_absolute_path(value):
                raise ScenarioValidationError(
                    f"step {index} goto must be an absolute path beginning with '/'"
                )
            if action == "click" and not isinstance(step.get("navigates", False), bool):
                raise ScenarioValidationError(f"step {index} click navigates must be true or false")
        elif action == "wait_for":
            if isinstance(value, str):
                if not value.strip():
                    raise ScenarioValidationError(f"step {index} wait_for cannot be empty")
            elif (
                not isinstance(value, dict)
                or not isinstance(value.get("selector"), str)
                or not value["selector"].strip()
            ):
                raise ScenarioValidationError(
                    f"step {index} wait_for must be a selector string or rule object"
                )
            elif not isinstance(value.get("absent", []), list) or not all(
                isinstance(selector, str) and selector.strip()
                for selector in value.get("absent", [])
            ):
                raise ScenarioValidationError(
                    f"step {index} wait_for absent must be a list of selectors"
                )
            elif "text" in value and (not isinstance(value["text"], str) or not value["text"]):
                raise ScenarioValidationError(f"step {index} wait_for text must be a non-empty string")
            elif not isinstance(value.get("text_not_in", []), list) or not all(
                isinstance(text, str) for text in value.get("text_not_in", [])
            ):
                raise ScenarioValidationError(
                    f"step {index} wait_for text_not_in must be a list of strings"
                )
        elif action in {"record_current_article", "assert_current_article"}:
            if not isinstance(value, dict):
                raise ScenarioValidationError(f"step {index} {action} must be an object")
            for selector_key in ("title_selector", "url_selector"):
                if not isinstance(value.get(selector_key), str) or not value[selector_key].strip():
                    raise ScenarioValidationError(
                        f"step {index} {action} requires {selector_key}"
                    )
        elif action == "screenshot":
            if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
                raise ScenarioValidationError(
                    f"step {index} screenshot name must be a lowercase hyphenated slug"
                )
            if value in seen_screenshot_names:
                raise ScenarioValidationError(f"duplicate screenshot scene: {value}")
            screenshot_names.append(value)
            seen_screenshot_names.add(value)
            screenshot_count += 1
            for text_key in ("title", "description", "alt"):
                if not isinstance(step.get(text_key), str) or not step[text_key].strip():
                    raise ScenarioValidationError(
                        f"step {index} screenshot requires non-empty {text_key}"
                    )

        elif action == "free_capture":
            if not isinstance(value, dict):
                raise ScenarioValidationError(f"step {index} free_capture must be an object")
            if not isinstance(value.get("prefix"), str) or not SLUG_RE.fullmatch(value["prefix"]):
                raise ScenarioValidationError(
                    f"step {index} free_capture requires a lowercase hyphenated prefix"
                )
            if value["prefix"] in seen_screenshot_names:
                raise ScenarioValidationError(
                    f"step {index} free_capture prefix collides with a declared screenshot scene"
                )
            for text_key in ("title", "description", "alt", "instructions"):
                if text_key in value and (
                    not isinstance(value[text_key], str) or not value[text_key].strip()
                ):
                    raise ScenarioValidationError(
                        f"step {index} free_capture {text_key} must be a non-empty string if present"
                    )
            has_free_capture = True

        elif action in LOCAL_ONLY_ACTIONS and not unauthenticated:
            raise ScenarioValidationError(
                f"step {index} {action} is allowed only with auth_profile 'none' (local capture)"
            )
        if action == "fill":
            if not isinstance(value, str) or not value.strip() or not isinstance(step.get("value"), str):
                raise ScenarioValidationError(
                    f"step {index} fill needs a selector and a string 'value'"
                )
        elif action == "press":
            if not isinstance(value, str) or not KEY_RE.fullmatch(value):
                raise ScenarioValidationError(f"step {index} press must name one key, e.g. 'Escape'")
            if "selector" in step and (not isinstance(step["selector"], str) or not step["selector"].strip()):
                raise ScenarioValidationError(f"step {index} press selector must be a non-empty string")
        elif action == "evaluate":
            if not isinstance(value, str) or not value.strip():
                raise ScenarioValidationError(f"step {index} evaluate must be a non-empty script")
        elif action == "route":
            _validate_route(value, index)
        elif action == "unroute":
            if not isinstance(value, str) or not value.strip():
                raise ScenarioValidationError(f"step {index} unroute must be a URL pattern")
        elif action == "sample":
            if not isinstance(value, str) or not SAMPLE_ACTION_RE.fullmatch(value):
                raise ScenarioValidationError(f"step {index} sample must name a sample-server action")
            if not isinstance(step.get("args", {}), dict):
                raise ScenarioValidationError(f"step {index} sample args must be an object")

        if action in {"operator", "free_capture"}:
            operator_count += 1

    if screenshot_count == 0 and not has_free_capture:
        raise ScenarioValidationError("scenario must contain at least one screenshot")

    profile = data["device_profile"]
    domain = data["domain"]
    if not has_free_capture:
        # free_capture produces an unbounded number of scenes chosen live, so
        # the full output-filename set can't be known ahead of time — only
        # scenarios without it can be exhaustively pre-validated this way.
        expected = {
            output_filename(order, domain, scene, profile, extension)
            for order, scene in enumerate(screenshot_names, start=1)
            for extension in ("png", "webp")
        }
        if len(expected) != screenshot_count * 2:
            raise ScenarioValidationError("scenario produces duplicate output filenames")

    data["_summary"] = {
        "screenshots": screenshot_count,
        "operator_pauses": operator_count,
    }
    return data


def load_scenario(path: Path | str) -> dict[str, Any]:
    """Load and validate a JSON scenario."""
    scenario_path = Path(path)
    try:
        data = json.loads(scenario_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioValidationError(f"scenario not found: {scenario_path}") from exc
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(
            f"invalid JSON in {scenario_path}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(data, dict):
        raise ScenarioValidationError("scenario root must be an object")
    return validate_scenario(data)
