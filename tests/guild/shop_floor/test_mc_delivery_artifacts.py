"""Downloads, 26 Oct (Robert's prod session): plain owner wording asks for a download, and a file MC already saved (a
verified artifact) is delivered from the platform's own record instead of being re-typed by the model."""
import json

import pytest

from minimoi_portal.guild_ui.mc_artifact_bundle import BundleRefused
from minimoi_portal.guild_ui.mc_deliveries import from_artifacts, requested

REF = "e8b64cb2-7538-be80-93cf-bf7930901d8a"


@pytest.mark.parametrize("text", [
    "can out just put a file here as attachment and I download it from the browser now?",   # verbatim, typo included
    "Can you put a file here as attachment and I download it from the browser",
    "Give me the transcript so I can download it.",
    "Attach the report as an attachment.",
    "Create a downloadable file with the transcript.",
])
def test_plain_owner_wording_asks_for_a_download(text):
    assert requested(text)


@pytest.mark.parametrize("text", [
    "How do downloads work?", "Can you explain attachments?", "I downloaded it yesterday.",
    "> put the file as attachment", 'He said "send it as a download"', "```\nput the file as attachment\n```",
])
def test_questions_reports_and_quoted_text_are_not_requests(text):
    assert not requested(text)


def test_a_named_artifact_is_built_from_the_platforms_record_not_the_model():
    seen = []

    def files(refs):
        seen.append(refs)
        return [{"name": "mc-transcript.md", "content": "verified bytes"}]
    out = json.loads(from_artifacts(json.dumps({"artifacts": [REF]}), files))
    assert seen == [[REF]] and out == {"files": [{"name": "mc-transcript.md", "content": "verified bytes"}]}


@pytest.mark.parametrize("packet", [
    {"artifacts": []}, {"artifacts": ["not-a-uuid"]}, {"artifacts": [REF, REF]}, {"artifacts": [REF] * 7},
    {"artifacts": [REF], "files": []},
])
def test_malformed_artifact_packets_are_refused_or_left_to_the_bundle_validator(packet):
    text = json.dumps(packet)
    if set(packet) != {"artifacts"}:
        assert from_artifacts(text, lambda refs: []) == text          # prepare() refuses it as an invalid packet
    else:
        with pytest.raises(BundleRefused):
            from_artifacts(text, lambda refs: [{"name": "x.md", "content": "x"}])


def test_an_unverified_or_foreign_artifact_yields_nothing_and_is_refused():
    with pytest.raises(BundleRefused):
        from_artifacts(json.dumps({"artifacts": [REF]}), lambda refs: [])
    with pytest.raises(BundleRefused):
        from_artifacts(json.dumps({"artifacts": [REF]}), None)         # MC tools off: no resolver


def test_a_files_packet_is_unchanged():
    text = json.dumps({"files": [{"name": "a.md", "content": "x"}]})
    assert from_artifacts(text, lambda refs: []) == text
