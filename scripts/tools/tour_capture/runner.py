"""Playwright orchestration for declared tour-capture scenarios."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
import json
from pathlib import Path
from shutil import copy2
from urllib.parse import urlparse

from .auth import ensure_authenticated, storage_state_path
from .imaging import build_contact_sheet, optimize_png
from .manifest import CapturedScene, write_manifest, write_report, write_review_page
from .readiness import wait_for_checkpoint
from .scenario import DEVICE_PROFILES, output_filename


ALLOWED_CAPTURE_HOSTS = {"localhost", "127.0.0.1", "dev.minimoi.ai"}
CAPTURE_STYLE = """
html { scrollbar-width: none !important; }
::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
"""


class CaptureRunError(RuntimeError):
    """Raised when a capture run cannot safely complete."""


def validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in ALLOWED_CAPTURE_HOSTS
        or (parsed.hostname == "dev.minimoi.ai" and parsed.scheme != "https")
    ):
        raise CaptureRunError(
            "capture base URL must use HTTPS for dev.minimoi.ai, or HTTP/HTTPS "
            "for localhost or 127.0.0.1; production capture is refused"
        )
    return base_url.rstrip("/")


def _valid_storage_state(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("cookies"), list)


def _retain_last_capture_diagnostic(
    diagnostics_dir: Path,
    captured_specs: list[tuple[int, dict, Path]],
) -> list[str]:
    if not (diagnostics_dir / "failure.png").exists():
        for _order, _step, raw_path in reversed(captured_specs):
            if raw_path.exists():
                with suppress(Exception):
                    copy2(raw_path, diagnostics_dir / "last-captured.png")
                break
    return sorted(path.name for path in diagnostics_dir.iterdir() if path.is_file())


class CaptureRunner:
    def __init__(
        self,
        scenario: dict,
        base_url: str,
        output_root: Path,
        *,
        headless: bool = False,
        timeout_ms: int = 20_000,
        quality: int = 92,
    ) -> None:
        self.scenario = scenario
        self.base_url = validate_base_url(base_url)
        self.output_root = output_root
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.quality = quality
        self.repo_root = Path(__file__).resolve().parents[3]
        self.records: dict[str, dict[str, str]] = {}
        self.last_action = "not started"

    def _new_run_dir(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        run_dir = self.output_root / self.scenario["id"] / stamp
        for name in ("raw", "optimized", "diagnostics"):
            (run_dir / name).mkdir(parents=True, exist_ok=True)
        return run_dir

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def _record_article(self, page, rule: dict) -> None:
        title = page.locator(rule["title_selector"]).first.inner_text().strip()
        url = page.locator(rule["url_selector"]).first.get_attribute("href") or ""
        if not title or not url:
            raise CaptureRunError("current article title or URL is empty")
        self.records["article"] = {"title": title, "url": url}

    def _assert_article(self, page, rule: dict) -> None:
        expected = self.records.get("article")
        if not expected:
            raise CaptureRunError("article identity was not recorded before assertion")
        actual_title = page.locator(rule["title_selector"]).first.inner_text().strip()
        actual_url = page.locator(rule["url_selector"]).first.get_attribute("href") or ""
        if actual_title != expected["title"] or actual_url != expected["url"]:
            raise CaptureRunError("the open article changed after operator selection")

    def run(self) -> Path:
        if self.headless and self.scenario["_summary"]["operator_pauses"]:
            raise CaptureRunError("operator-assisted scenarios cannot run headless")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CaptureRunError(
                "Playwright is not installed; install requirements and Chromium first"
            ) from exc

        run_dir = self._new_run_dir()
        raw_dir = run_dir / "raw"
        optimized_dir = run_dir / "optimized"
        diagnostics_dir = run_dir / "diagnostics"
        state_path = storage_state_path(self.repo_root, self.scenario["auth_profile"])
        profile = DEVICE_PROFILES[self.scenario["device_profile"]]
        captured_specs: list[tuple[int, dict, Path]] = []
        current_url = "unavailable"
        started_at = datetime.now(timezone.utc).isoformat()

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context_args = {
                    "viewport": {"width": profile.width, "height": profile.height},
                    "device_scale_factor": profile.device_scale_factor,
                    "locale": self.scenario.get("locale", "en-US"),
                    "color_scheme": "light",
                    "reduced_motion": "reduce",
                }
                if _valid_storage_state(state_path):
                    state_path.chmod(0o600)
                    context_args["storage_state"] = str(state_path)
                context = browser.new_context(**context_args)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                try:
                    ensure_authenticated(
                        page,
                        context,
                        self.base_url,
                        self.scenario["start_path"],
                        self.scenario["auth_profile"],
                        state_path,
                    )

                    order = 0
                    for index, step in enumerate(self.scenario["steps"], start=1):
                        action = next(key for key in step if key in {
                            "goto", "click", "wait_for", "operator",
                            "record_current_article", "assert_current_article", "screenshot"
                        })
                        self.last_action = f"step {index}: {action}"
                        value = step[action]
                        if action == "goto":
                            page.goto(
                                self._url(value),
                                wait_until="domcontentloaded",
                                timeout=self.timeout_ms,
                            )
                        elif action == "click":
                            page.locator(value).first.click(timeout=self.timeout_ms)
                        elif action == "wait_for":
                            wait_for_checkpoint(page, value, self.timeout_ms)
                        elif action == "operator":
                            print(f"\nOPERATOR ACTION\n{value}\n")
                            input("Press Enter when the browser is ready to continue: ")
                        elif action == "record_current_article":
                            self._record_article(page, value)
                        elif action == "assert_current_article":
                            self._assert_article(page, value)
                        elif action == "screenshot":
                            order += 1
                            filename = output_filename(
                                order,
                                self.scenario["domain"],
                                value,
                                profile.name,
                                "png",
                            )
                            raw_path = raw_dir / filename
                            page.screenshot(
                                path=str(raw_path),
                                full_page=False,
                                animations="disabled",
                                scale="device",
                                style=CAPTURE_STYLE,
                            )
                            captured_specs.append((order, step, raw_path))
                    current_url = page.url
                except Exception:
                    current_url = page.url
                    try:
                        page.screenshot(
                            path=str(diagnostics_dir / "failure.png"),
                            full_page=False,
                            animations="disabled",
                            scale="device",
                            style=CAPTURE_STYLE,
                        )
                    except Exception:
                        pass
                    raise
                finally:
                    with suppress(Exception):
                        context.close()
                    with suppress(Exception):
                        browser.close()

            scenes: list[CapturedScene] = []
            contact_inputs: list[tuple[str, Path]] = []
            for order, step, raw_path in captured_specs:
                optimized_path = optimize_png(
                    raw_path,
                    optimized_dir,
                    profile.output_dimensions,
                    self.quality,
                )
                relative_optimized = optimized_path.relative_to(run_dir).as_posix()
                relative_raw = raw_path.relative_to(run_dir).as_posix()
                scene = CapturedScene(
                    order=order,
                    scene=step["screenshot"],
                    title=step["title"],
                    description=step["description"],
                    alt=step["alt"],
                    raw=relative_raw,
                    optimized=relative_optimized,
                    width=profile.output_dimensions[0],
                    height=profile.output_dimensions[1],
                    bytes=optimized_path.stat().st_size,
                )
                scenes.append(scene)
                contact_inputs.append((f"{order:02d} {scene.title}", optimized_path))

            write_manifest(run_dir, self.scenario, scenes, self.records)
            build_contact_sheet(contact_inputs, run_dir / "contact-sheet.webp")
            review_path = write_review_page(run_dir, self.scenario, scenes)
            write_report(
                run_dir,
                {
                    "status": "complete",
                    "scenario": self.scenario["id"],
                    "base_url": self.base_url,
                    "started_at": started_at,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "scene_count": len(scenes),
                    "outputs": [
                        {
                            "order": scene.order,
                            "raw": scene.raw,
                            "optimized": scene.optimized,
                            "width": scene.width,
                            "height": scene.height,
                            "bytes": scene.bytes,
                        }
                        for scene in scenes
                    ],
                    "records": self.records,
                },
            )
            return review_path
        except Exception as exc:
            diagnostic_artifacts = _retain_last_capture_diagnostic(
                diagnostics_dir,
                captured_specs,
            )
            write_report(
                run_dir,
                {
                    "status": "failed",
                    "scenario": self.scenario["id"],
                    "base_url": self.base_url,
                    "started_at": started_at,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "last_action": self.last_action,
                    "current_url": current_url,
                    "error": str(exc),
                    "diagnostic_artifacts": diagnostic_artifacts,
                    "records": self.records,
                },
            )
            raise CaptureRunError(
                f"capture failed at {self.last_action}: {exc}; diagnostics: {diagnostics_dir}"
            ) from exc
