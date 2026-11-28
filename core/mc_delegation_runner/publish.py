"""Publishing a finished build (#693): push one branch and open one draft pull request. Runner-side only.

Robert's decisions of 27 Nov 2026: the runner may push a branch and open a DRAFT pull request after a job he
confirmed; nothing merges. The credential is whatever the ``token`` callable returns, and this module does not care
where it comes from (cli.py: for now the Mac's own ``gh`` login, read with ``gh auth token`` at publish time; or the
narrower choice, a token limited to this one repository, Contents: write and Pull requests: write, in the login
Keychain). Whatever the token could do elsewhere, this module uses it for one repository, one branch and one draft.

This module is self-contained on purpose (stdlib only, no import from the rest of the runner): issue #695 replaces it
with a central GitHub service. Its whole interface is ``Publisher(slug, token, ...)`` and
``Publisher.publish(task_dir, branch, *, title, body, base_branch="main", commit=None) -> dict``, which returns
``{"state": "published", "pr_url", "pr_number"}`` or ``{"state": "refused" | "push_failed" | "pr_failed", "code"}``.

What this module does, and what it never does:

* It runs after the agent has exited, in the runner's own process. The agent never has the token: it is not in the
  envelope, not in the agent's environment, and the agent's checkout has no remote.
* It never runs a git command that holds the token inside the agent's checkout, because that checkout's hooks and
  configuration were writable by the agent. The branch is first fetched (no token in that call) into a new bare
  repository the runner makes, and the push runs from there, with hooks off and none of the Mac user's git
  configuration.
* It pushes exactly one ref, ``refs/heads/mc/<8 hex>-g<generation>``, to ``https://github.com/<owner>/<name>.git``.
  Any other branch name is refused before anything runs. Never a force push, never a tag, never a delete.
* The token reaches git only through that one subprocess's environment (``GIT_CONFIG_COUNT``/``KEY``/``VALUE``: an
  ``Authorization`` header for github.com only, and an empty ``credential.helper``). It is never on a command line,
  never in a remote URL or a config file, and never returned or logged: results carry fixed codes only, and git's
  own output is read for a code and then dropped.
* The pull request is a draft. An existing pull request for the branch is found first and returned, so a second run
  for the same branch (a resumed task) opens nothing new.
* No merge, no review, no label, no release, no deploy: there is no code here for any of them.

Stdlib only; Python 3.9-compatible (the installed runner runs under /usr/bin/python3).
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BRANCH = re.compile(r"^mc/[0-9a-f]{8}-g[1-9][0-9]{0,8}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_BASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_\-.=]{20,512}$")
API = "https://api.github.com"
TITLE_MAX, BODY_MAX, ANSWER_MAX = 200, 60_000, 2 * 1024 * 1024
PUSH_TIMEOUT_S, FETCH_TIMEOUT_S, HTTP_TIMEOUT_S = 90, 60, 20


def scrub(text, token) -> str:
    """``text`` without the token in either form it is ever sent in."""
    text = text if isinstance(text, str) else ""
    if isinstance(token, str) and token:
        text = text.replace(token, "[token]").replace(_basic(token), "[token]")
    return text


def _basic(token: str) -> str:
    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


def _run(argv, *, cwd, env, timeout):
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, errors="replace")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    return r.returncode, r.stdout, r.stderr


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):          # an Authorization header is never carried to another address
        return None


def _http(method, url, headers, body, timeout):
    """(status, parsed JSON or None). A refusal by GitHub is a status, never an exception."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as r:
            status, raw = r.status, r.read(ANSWER_MAX + 1)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, b""
    if len(raw) > ANSWER_MAX:
        return status, None
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return status, None


def _push_code(returncode, stderr) -> str:
    low = (stderr or "").lower()
    if returncode == 124:
        return "timeout"
    if "non-fast-forward" in low or "[rejected]" in low or "fetch first" in low:
        return "rejected_not_fast_forward"            # the branch exists with other history: never forced
    if "authentication failed" in low or "403" in low or "401" in low or "permission to" in low:
        return "auth_failed"
    if "not found" in low:
        return "repository_not_found"
    if "could not resolve host" in low or "unable to access" in low or "failed to connect" in low:
        return "unreachable"
    return f"git_exit_{returncode}" if isinstance(returncode, int) else "git_failed"


class Publisher:
    """``slug`` is ``owner/name``. ``token`` is a callable returning the token, or None when there is none (called
    when a publish starts, so the runner holds it in memory only for that one call). ``run(argv, cwd=, env=, timeout=) -> (returncode, stdout, stderr)`` and
    ``http(method, url, headers, body, timeout) -> (status, json)`` are replaced by fakes in tests. ``base_env`` is
    the scrubbed environment git starts from (workspace.child_env()). ``remote_url`` is for tests against a local
    bare repository; left out, it is github.com's address for ``slug``."""

    def __init__(self, slug, token, *, run=None, http=None, base_env=None, remote_url=None, api=API, workdir=None):
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or ".." in slug or slug.endswith(".git"):
            raise ValueError("publish_repository_invalid")
        if not callable(token):
            raise ValueError("publish_token_source_invalid")
        self.slug, self._token = slug, token
        self._run, self._http = run or _run, http or _http
        self._base_env = base_env
        self.remote_url = remote_url or f"https://github.com/{slug}.git"
        self.api, self.workdir = api.rstrip("/"), workdir

    def _env(self) -> dict:
        base = self._base_env() if callable(self._base_env) else dict(self._base_env or {"PATH": "/usr/bin:/bin"})
        # No git configuration of the Mac user's or the system's: no credential helper, no URL rewriting, no hooks path.
        return {**base, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}

    def publish(self, task_dir, branch, *, title, body, base_branch="main", commit=None) -> dict:
        """Push ``branch`` and open (or find) its draft pull request. ``{"state": "published", "pr_url",
        "pr_number"}``, or ``{"state": "refused" | "push_failed" | "pr_failed", "code"}``. ``commit``: the exact
        commit the branch must point at; anything else is not pushed."""
        if not isinstance(branch, str) or not BRANCH.fullmatch(branch):
            return {"state": "refused", "code": "branch_not_allowed"}
        if not isinstance(base_branch, str) or not _BASE.fullmatch(base_branch) or ".." in base_branch \
                or base_branch == branch:
            return {"state": "refused", "code": "base_branch_invalid"}
        if commit is not None and not (isinstance(commit, str) and _SHA.fullmatch(commit)):
            return {"state": "refused", "code": "commit_invalid"}
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            return {"state": "refused", "code": "pull_request_text_invalid"}
        try:
            token = self._token()
        except Exception:
            token = None
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return {"state": "refused", "code": "no_publish_credential"}
        title = " ".join(title.split())[:TITLE_MAX]
        stage = Path(tempfile.mkdtemp(prefix="mc-publish-", dir=self.workdir))
        try:
            try:
                pushed = self._push(stage, Path(task_dir), branch, commit, token)
            except Exception as exc:                      # a fixed code; never the exception's text
                return {"state": "push_failed", "code": f"error_{type(exc).__name__}"}
            if pushed is not None:
                return pushed
            try:
                return self._pull_request(branch, base_branch, title, body[:BODY_MAX], token)
            except Exception as exc:
                return {"state": "pr_failed", "code": f"error_{type(exc).__name__}"}
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _push(self, stage, task_dir, branch, commit, token):
        """None when the branch is on GitHub at this commit; else the failure."""
        ref = f"refs/heads/{branch}"
        repo, env = str(stage / "repo.git"), self._env()
        git = ["git", "-c", "core.hooksPath=/dev/null"]
        steps = ((git + ["init", "--quiet", "--bare", repo], None, 30),
                 # The agent's checkout is only ever read here, by git's own upload-pack, and with no token.
                 (git + ["--git-dir", repo, "fetch", "--quiet", "--no-tags", str(task_dir), f"{ref}:{ref}"], None,
                  FETCH_TIMEOUT_S))
        for argv, cwd, timeout in steps:
            code, _, _ = self._run(argv, cwd=cwd, env=env, timeout=timeout)
            if code != 0:
                return {"state": "push_failed", "code": "staging_failed"}
        code, out, _ = self._run(git + ["--git-dir", repo, "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=None,
                                 env=env, timeout=30)
        head = (out or "").strip()
        if code != 0 or not _SHA.fullmatch(head) or (commit is not None and head != commit):
            return {"state": "push_failed", "code": "commit_mismatch"}
        secret = {"GIT_CONFIG_COUNT": "3",
                  "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
                  "GIT_CONFIG_KEY_1": "http.https://github.com/.extraHeader",
                  "GIT_CONFIG_VALUE_1": f"Authorization: Basic {_basic(token)}",
                  "GIT_CONFIG_KEY_2": "core.hooksPath", "GIT_CONFIG_VALUE_2": "/dev/null"}
        # One ref, no "+", no --force, no --tags, no --mirror, no --delete.
        argv = ["git", "--git-dir", repo, "push", "--quiet", "--no-verify", self.remote_url, f"{ref}:{ref}"]
        code, _, err = self._run(argv, cwd=None, env={**env, **secret}, timeout=PUSH_TIMEOUT_S)
        if code != 0:
            return {"state": "push_failed", "code": _push_code(code, scrub(err, token))}
        return None

    def _headers(self, token) -> dict:
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "minimoi-delegation-runner",
                "Content-Type": "application/json"}

    def _existing(self, branch, token):
        """The pull request already open (or closed) for this branch, as its number, or None. (status, number)."""
        owner = self.slug.split("/", 1)[0]
        url = f"{self.api}/repos/{self.slug}/pulls?head={owner}:{branch}&state=all&per_page=5"
        status, doc = self._http("GET", url, self._headers(token), None, HTTP_TIMEOUT_S)
        if status != 200 or not isinstance(doc, list):
            return status, None
        for pr in doc:
            head = pr.get("head") if isinstance(pr, dict) else None
            number = pr.get("number") if isinstance(pr, dict) else None
            if isinstance(head, dict) and head.get("ref") == branch and type(number) is int and number > 0:
                return status, number
        return status, None

    def _published(self, number) -> dict:
        return {"state": "published", "pr_url": f"https://github.com/{self.slug}/pull/{number}", "pr_number": number}

    def _pull_request(self, branch, base_branch, title, body, token) -> dict:
        status, number = self._existing(branch, token)
        if number is not None:
            return self._published(number)
        if status != 200:
            return {"state": "pr_failed", "code": f"lookup_http_{status}"}
        payload = json.dumps({"title": title, "head": branch, "base": base_branch, "body": body,
                              "draft": True}).encode()
        status, doc = self._http("POST", f"{self.api}/repos/{self.slug}/pulls", self._headers(token), payload,
                                 HTTP_TIMEOUT_S)
        number = doc.get("number") if isinstance(doc, dict) else None
        if status == 201 and type(number) is int and number > 0:
            return self._published(number)
        if status == 422:                                 # GitHub says one exists already: find it, open nothing
            _, number = self._existing(branch, token)
            if number is not None:
                return self._published(number)
        return {"state": "pr_failed", "code": f"http_{status}"}
