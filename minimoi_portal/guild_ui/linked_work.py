"""Linked work: the one spec or issue a conversation is explicitly tied to.

Empty until Robert attaches something. Nothing here reads browsing history,
calls a model, or fetches a URL: a pasted link is only checked for shape, and
only links into this repository on GitHub are accepted. A number is looked up
in the Build Log first (the queue the portal already holds); a number the
Build Log does not know is shown as a GitHub issue link whose title is not
loaded, never as an item that was found.
"""
from __future__ import annotations

import re

REPO = "robertvanstedum/personal-ai-agents"
GITHUB = f"https://github.com/{REPO}"

_NUMBER = re.compile(r"^#?(\d{1,6})$")
_ISSUE = re.compile(rf"^https://github\.com/{re.escape(REPO)}/(issues|pull)/(\d{{1,6}})/?(?:\?[^#\s]*)?(?:#(\S*))?$")
_FILE = re.compile(rf"^https://github\.com/{re.escape(REPO)}/blob/main/([A-Za-z0-9][A-Za-z0-9._/-]{{0,200}})(?:\?[^#\s]*)?(?:#(\S*))?$")
# The only fragments kept: a line or line range in a file, a comment on an issue or pull request, a review comment.
_FRAGMENT = re.compile(r"^(?:L\d{1,6}(?:-L\d{1,6})?|issuecomment-\d{1,12}|discussion_r\d{1,12})$")

WRONG_PLACE = ("Only an issue, pull request or file in robertvanstedum/personal-ai-agents on GitHub, "
               "or a number, can be linked here.")


class LinkRefused(ValueError):
    """The text is not something this conversation may be linked to."""


def _fragment(raw) -> str:
    return raw if raw and _FRAGMENT.fullmatch(raw) else ""


def parse(text) -> tuple[str, str, str]:
    """(kind, value, fragment): ('number', '123', '') | ('issue' or 'pull', '123', fragment) |
    ('file', 'docs/x.md', fragment). The kind and a valid fragment of a pasted link are kept exactly;
    anything else after the address is dropped. Raises LinkRefused."""
    if not isinstance(text, str):
        raise LinkRefused("Enter a number or paste a GitHub link.")
    value = text.strip()
    if not value:
        raise LinkRefused("Enter a number or paste a GitHub link.")
    if len(value) > 400:
        raise LinkRefused(WRONG_PLACE)
    m = _NUMBER.match(value)
    if m and int(m.group(1)) > 0:
        return "number", str(int(m.group(1))), ""
    m = _ISSUE.match(value)
    if m and int(m.group(2)) > 0:
        return m.group(1).replace("issues", "issue"), str(int(m.group(2))), _fragment(m.group(3))
    m = _FILE.match(value)
    if m and ".." not in m.group(1).split("/") and "//" not in m.group(1):
        return "file", m.group(1), _fragment(m.group(2))
    raise LinkRefused(WRONG_PLACE)


def issue_url(number: str, fragment: str = "", pull: bool = False) -> str:
    return f"{GITHUB}/{'pull' if pull else 'issues'}/{number}" + (f"#{fragment}" if fragment else "")


def file_url(path: str, fragment: str = "") -> str:
    return f"{GITHUB}/blob/main/{path}" + (f"#{fragment}" if fragment else "")


def work_item(kind: str, value: str, fragment: str = "", *, queue_item: dict | None = None,
              item_href: str | None = None) -> dict:
    """The stored shape. Every field is chosen here, never copied from the pasted text."""
    if kind == "file":
        name = value.rsplit("/", 1)[-1] + (f"#{fragment}" if fragment else "")
        return {"kind": "github_file", "ref": value, "label": name[:120], "href": file_url(value, fragment),
                "source": "link"}
    if kind == "number" and queue_item and queue_item.get("title") and item_href:   # a pasted GitHub link is never reinterpreted
        label = f"#{value} {queue_item['title']}"
        return {"kind": "item", "ref": value, "label": label[:120], "href": item_href,
                "source": "number"}
    # What GitHub kind it is: exactly what a pasted link said; a bare number could be either.
    gh = "pull" if kind == "pull" else "issue" if kind == "issue" else "either"
    return {"kind": "github", "github_kind": gh, "ref": value, "label": f"#{value}",
            "href": issue_url(value, fragment, pull=gh == "pull"),
            "source": "number" if kind == "number" else "link", "title_loaded": False}
