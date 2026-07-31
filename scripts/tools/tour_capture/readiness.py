"""Deterministic page-readiness checks for screenshot checkpoints."""

from __future__ import annotations

from typing import Any


class CaptureReadinessError(RuntimeError):
    """Raised when a declared checkpoint state does not become stable."""


def _rule(value: str | dict[str, Any]) -> dict[str, Any]:
    return {"selector": value} if isinstance(value, str) else value


def _wait_for_visible_images(page, timeout_ms: int) -> None:
    """Wait for visible image elements and CSS background images to settle."""
    page.evaluate(
        r"""async (timeoutMs) => {
          const loadAll = async () => {
            const visible = (el) => {
              const style = getComputedStyle(el);
              const rect = el.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' &&
                Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0 &&
                rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight &&
                rect.left < innerWidth;
            };

            const imageElements = [...document.images].filter(visible);
            await Promise.all(imageElements.map((img) => {
              if (img.complete) return Promise.resolve();
              return new Promise((resolve) => {
                img.addEventListener('load', resolve, {once: true});
                img.addEventListener('error', resolve, {once: true});
              });
            }));
            const broken = imageElements.find((img) => !img.complete || img.naturalWidth === 0);
            if (broken) throw new Error(`image failed to load: ${broken.currentSrc || broken.src}`);

            const backgroundUrls = new Set();
            for (const el of [...document.querySelectorAll('*')].filter(visible)) {
              for (const pseudo of [null, '::before', '::after']) {
                const style = getComputedStyle(el, pseudo);
                if (style.display === 'none' || style.visibility === 'hidden' ||
                    Number(style.opacity || 1) <= 0) continue;
                const value = style.backgroundImage || '';
                for (const match of value.matchAll(/url\((['"]?)(.*?)\1\)/g)) {
                  if (match[2]) backgroundUrls.add(new URL(match[2], document.baseURI).href);
                }
              }
            }
            await Promise.all([...backgroundUrls].map((url) => new Promise((resolve, reject) => {
              const img = new Image();
              img.onload = resolve;
              img.onerror = () => reject(new Error(`background image failed to load: ${url}`));
              img.src = url;
            })));
          };
          await Promise.race([
            loadAll(),
            new Promise((_, reject) => setTimeout(
              () => reject(new Error('visible images did not settle before timeout')),
              timeoutMs,
            )),
          ]);
        }""",
        arg=timeout_ms,
    )


def wait_for_checkpoint(page, value: str | dict[str, Any], timeout_ms: int = 20_000) -> None:
    """Wait for a stable, visible state and reject horizontal overflow."""
    rule = _rule(value)
    selector = rule["selector"]
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        page.evaluate("async () => { if (document.fonts) await document.fonts.ready; }")
        _wait_for_visible_images(page, timeout_ms)

        for absent_selector in rule.get("absent", []):
            page.locator(absent_selector).first.wait_for(state="hidden", timeout=timeout_ms)

        text_selector = rule.get("text_selector")
        if text_selector:
            disallowed = rule.get("text_not_in", [])
            page.wait_for_function(
                """({selector, disallowed}) => {
                  const el = document.querySelector(selector);
                  if (!el) return false;
                  const value = (el.textContent || '').trim();
                  return value.length > 0 && !disallowed.includes(value);
                }""",
                arg={"selector": text_selector, "disallowed": disallowed},
                timeout=timeout_ms,
            )

        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        if overflow > 1:
            raise CaptureReadinessError(
                f"horizontal overflow is {overflow}px at checkpoint selector {selector!r}"
            )
    except CaptureReadinessError:
        raise
    except Exception as exc:
        raise CaptureReadinessError(
            f"checkpoint did not become ready for selector {selector!r}: {exc}"
        ) from exc
