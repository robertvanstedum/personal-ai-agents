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
    "operator",
    "record_current_article",
    "assert_current_article",
    "screenshot",
}
AUTH_PROFILES = {"owner_session"}


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
    if data["device_profile"] not in DEVICE_PROFILES:
        raise ScenarioValidationError(f"unknown device profile: {data['device_profile']!r}")
    if data["auth_profile"] not in AUTH_PROFILES:
        raise ScenarioValidationError(f"unknown auth profile: {data['auth_profile']!r}")
    if not isinstance(data["start_path"], str) or not (
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
    for index, step in enumerate(data["steps"], start=1):
        if not isinstance(step, dict):
            raise ScenarioValidationError(f"step {index} must be an object")
        action = _action_key(step, index)
        value = step[action]

        if action in {"goto", "click", "operator"}:
            if not isinstance(value, str) or not value.strip():
                raise ScenarioValidationError(f"step {index} {action} must be a non-empty string")
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

        if action == "operator":
            operator_count += 1

    if screenshot_count == 0:
        raise ScenarioValidationError("scenario must contain at least one screenshot")

    profile = data["device_profile"]
    domain = data["domain"]
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
