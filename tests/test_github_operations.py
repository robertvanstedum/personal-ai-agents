"""GitHub issue operations against a fake GitHub API (no network, no token)."""
import pytest

from core.github_operations import GitHubIssues, GitHubOpError

REPO = "robertvanstedum/personal-ai-agents"
OP = "op-gh-20261021-01"


class FakeGitHub:
    def __init__(self):
        self.issues = {7: {"number": 7, "title": "Prototype Lab UI", "state": "open",
                           "html_url": "https://github.com/x/7", "body": "b", "comments": 0}}
        self.comments = {7: []}
        self.calls = []
        self.fail_post = None      # "timeout_after_commit" | "timeout_before_commit" | "502_after_commit"
        self.tokens = []
        self.page_fail = None

    def __call__(self, method, url, headers, params, body):
        self.calls.append((method, url))
        self.tokens.append(headers["Authorization"])
        path = url.split("api.github.com", 1)[1]
        if method == "GET" and path == "/search/issues":
            q = params["q"]
            items = [dict(i) for i in self.issues.values() if "prototype" in q.lower()]
            items.append({"number": 99, "title": "a PR", "pull_request": {}})
            return 200, {"total_count": len(items), "incomplete_results": False, "items": items}
        if method == "GET" and path.endswith("/comments"):
            n = int(path.split("/")[-2])
            if n not in self.comments:
                return 404, {}
            page, per = params.get("page", 1), params.get("per_page", 100)
            if self.page_fail == page:
                return 502, {}
            return 200, self.comments[n][(page - 1) * per: page * per]
        if method == "GET" and path.startswith(f"/repos/{REPO}/issues/"):
            n = int(path.rsplit("/", 1)[1])
            return (200, self.issues[n]) if n in self.issues else (404, {})
        if method == "GET" and path == f"/repos/{REPO}/issues":
            page, per = params.get("page", 1), params.get("per_page", 100)
            mine = [i for i in sorted(self.issues.values(), key=lambda i: -i["number"])
                    if (i.get("user") or {}).get("login") == params.get("creator")]
            return 200, mine[(page - 1) * per: page * per]
        if method == "POST":
            if self.fail_post == "timeout_before_commit":
                raise TimeoutError()
            if path.endswith("/comments"):
                n = int(path.split("/")[-2])
                if n not in self.comments:
                    return 404, {}
                c = {"body": body["body"], "html_url": f"https://github.com/x/{n}#c{len(self.comments[n])}",
                     "user": {"login": "minimoi-bot"}}
                self.comments[n].append(c)
                result = (201, c)
            else:
                n = max(self.issues) + 1
                self.issues[n] = {"number": n, "title": body["title"], "body": body["body"], "state": "open",
                                  "html_url": f"https://github.com/x/{n}", "comments": 0,
                                  "user": {"login": "minimoi-bot"}}
                self.comments[n] = []
                result = (201, self.issues[n])
            if self.fail_post == "timeout_after_commit":
                self.fail_post = None
                raise TimeoutError()
            if self.fail_post == "502_after_commit":
                self.fail_post = None
                return 502, {}
            return result
        raise AssertionError((method, path))


@pytest.fixture
def gh():
    fake = FakeGitHub()
    return GitHubIssues(REPO, fake, lambda: "test-token", publisher_login="minimoi-bot"), fake


def test_search_is_scoped_bounded_and_excludes_prs(gh):
    api, fake = gh
    res = api.search('prototype lab "ui"')
    assert [i["number"] for i in res["items"]] == [7] and res["complete"]
    with pytest.raises(GitHubOpError):
        api.search("x" * 300)


def test_comment_needs_exact_approval_and_is_not_duplicated(gh):
    api, fake = gh
    p = api.prepare_comment(7, "Banner crops the head; Run it does nothing.", operation_id=OP)
    assert fake.comments[7] == []                         # preparing sends nothing
    assert api.execute(p, "0" * 64).code == "approval_does_not_match_proposal"
    out = api.execute(p, p.digest)
    assert out.status == "done" and out.url.startswith("https://github.com/x/7")
    assert "<!-- mc-op:" in fake.comments[7][0]["body"]
    again = api.execute(p, p.digest)
    assert again.status == "already_applied" and len(fake.comments[7]) == 1


def test_changed_text_needs_new_approval(gh):
    api, _ = gh
    p = api.prepare_comment(7, "original", operation_id=OP)
    p2 = api.prepare_comment(7, "edited", operation_id=OP)
    assert api.execute(p2, p.digest).status == "refused"


def test_uncertain_send_that_committed_is_found_by_marker(gh):
    api, fake = gh
    fake.fail_post = "timeout_after_commit"
    p = api.prepare_create("Prototype Lab: banner crop", "Three observations.", operation_id=OP)
    out = api.execute(p, p.digest)
    assert out.status == "already_applied" and out.code == "found_after_uncertain_send"
    assert len([i for i in fake.issues.values() if OP in i["body"]]) == 1


def test_uncertain_send_that_was_lost_is_unknown_not_retried(gh):
    api, fake = gh
    fake.fail_post = "timeout_before_commit"
    p = api.prepare_comment(7, "note", operation_id=OP)
    out = api.execute(p, p.digest)
    assert out.status == "unknown" and fake.comments[7] == []
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert len(posts) == 1                                # never blindly retried


@pytest.mark.parametrize("text, finding", [
    ("token ghp_abcdefghijklmnopqrstuvwxyz123456", "secret_token"),
    ("see /Users/vanstedum/Projects/x.md", "local_path"),
    ("db postgresql://user:pw@host/db", "connection_string"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private_key"),
])
def test_unsafe_public_content_cannot_execute(gh, text, finding):
    api, fake = gh
    p = api.prepare_comment(7, text, operation_id=OP)
    assert finding in p.findings
    out = api.execute(p, p.digest)
    assert out.status == "refused" and fake.comments[7] == []


def test_comment_on_missing_issue(gh):
    api, _ = gh
    p = api.prepare_comment(12345, "x", operation_id=OP)
    assert api.execute(p, p.digest).status == "not_found"


def test_input_validation(gh):
    api, _ = gh
    for kwargs in ({"number": True}, {"number": 0}, {"number": 1.5}):
        with pytest.raises(GitHubOpError):
            api.prepare_comment(kwargs["number"], "x", operation_id=OP)
    with pytest.raises(GitHubOpError):
        api.prepare_comment(7, "x", operation_id="short")
    with pytest.raises(GitHubOpError):
        api.prepare_create("", "x", operation_id=OP)
    with pytest.raises(ValueError):
        GitHubIssues("not a repo", lambda *a: (200, {}), lambda: "t", publisher_login="minimoi-bot")
    with pytest.raises(ValueError):
        GitHubIssues(REPO, lambda *a: (200, {}), lambda: "t", publisher_login="bad login!")


def test_token_is_never_in_outcomes(gh):
    api, fake = gh
    p = api.prepare_comment(7, "x", operation_id=OP)
    out = api.execute(p, p.digest)
    assert "test-token" not in repr(out) and "test-token" not in repr(p)


# ── Codex review G-F1 / G-F2 ────────────────────────────────────────────────
import dataclasses


def test_tampered_findings_flag_is_ignored_and_content_rescreened(gh):
    api, fake = gh
    p = api.prepare_comment(7, "see /Users/vanstedum/secret.md", operation_id=OP)
    forged = dataclasses.replace(p, findings=())
    out = api.execute(forged, forged.digest)
    assert out.status == "refused" and out.code.startswith("unsafe_content") and fake.comments[7] == []


def test_tampered_body_or_marker_is_refused(gh):
    api, _ = gh
    p = api.prepare_comment(7, "fine text", operation_id=OP)
    for change in ({"body": "fine text"}, {"kind": "close"}, {"repository": "other/repo"},
                   {"operation_id": "op-other-000001"}):
        q = dataclasses.replace(p, **change)
        assert api.execute(q, q.digest).status == "refused"


def test_same_operation_id_with_new_content_is_a_conflict_not_already_applied(gh):
    api, fake = gh
    first = api.prepare_comment(7, "version one", operation_id=OP)
    assert api.execute(first, first.digest).status == "done"
    second = api.prepare_comment(7, "version two", operation_id=OP)
    out = api.execute(second, second.digest)
    assert out.status == "refused" and out.code == "operation_id_used_for_different_content"
    assert len(fake.comments[7]) == 1


def test_marker_quoted_by_someone_else_is_not_ours(gh):
    api, fake = gh
    p = api.prepare_comment(7, "real note", operation_id=OP)
    fake.comments[7].append({"body": "quoting " + p.body, "html_url": "u", "user": {"login": "random-user"}})
    assert api.execute(p, p.digest).status == "done"


def test_marker_on_a_later_page_is_found(gh):
    api, fake = gh
    p = api.prepare_comment(7, "deep note", operation_id=OP)
    fake.comments[7] = [{"body": f"c{i}", "html_url": f"u{i}", "user": {"login": "x"}} for i in range(150)]
    fake.comments[7].append({"body": p.body, "html_url": "u-ours", "user": {"login": "minimoi-bot"}})
    out = api.execute(p, p.digest)
    assert out.status == "already_applied" and out.url == "u-ours"


def test_incomplete_reconciliation_does_not_post(gh):
    api, fake = gh
    p = api.prepare_comment(7, "note", operation_id=OP)
    fake.comments[7] = [{"body": f"c{i}", "html_url": f"u{i}", "user": {"login": "x"}} for i in range(150)]
    fake.page_fail = 2
    out = api.execute(p, p.digest)
    assert out.status == "unknown" and not any(c[0] == "POST" for c in fake.calls)


def test_5xx_after_acceptance_reconciles_instead_of_refusing(gh):
    api, fake = gh
    fake.fail_post = "502_after_commit"
    p = api.prepare_comment(7, "accepted despite 502", operation_id=OP)
    out = api.execute(p, p.digest)
    assert out.status == "already_applied" and len(fake.comments[7]) == 1
