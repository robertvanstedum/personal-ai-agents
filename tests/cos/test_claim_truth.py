"""Receipt truth for model replies (claim_truth.py): claims of a kept note or reminder, and promises
of a later action, are removed sentence by sentence; history, negatives and remarks stay."""
import pytest

from domains.cos.claim_truth import NOTICE, enforce, unsupported

UNSUPPORTED = [
    # promises of a later action by the assistant
    "I'll nudge you at 9.", "I'll remind you at 5.", "Got it, I'll remind you at 5.", "I'll ping you when it's time.",
    "I will remind you tomorrow morning.", "I'll send you a reminder at 9.", "You'll get a reminder then.",
    "I'll follow up tomorrow at 9.", "I'll get back to you on that tonight.", "You'll hear from me tomorrow.",
    "I'll make a note of that for later.", "I'll note it down.", "I'll add that to your notes.", "I'll note that.",
    # claims that something was kept or set
    "I've scheduled a reminder for 5 pm.", "Added to your notes.", "Reminder created for 5 pm.", "Consider it noted.",
    "I've jotted that down.", "Done — noted.", "Noted.", "Okay — noted that the deploy is Tuesday.",
    "Reminder set for 9am tomorrow.", "Okay, the reminder is set.", "I've set a reminder for Monday.",
    "I have set a reminder to call Anna.", "I've made a note of it.", "Made a note: dentist Tuesday.",
    "Done. I’ve saved that for you.", "Note saved through the platform-owned path.", "I've saved your note.",
    "Your note has been successfully recorded.", "Platform note recorded: loss of voice conversation.",
    "I've put it on your board.", "I've created a post-it for you.",
]
ORDINARY = [
    "Yes, it shipped in PR 323. I'll note that the backup cron on EC2 still needs the new script copied by hand.",
    "You have no reminders set right now.", "As I've noted before, we keep the old plist.",
    "There are no reminders set up in Google Calendar.", "Your dentist reminder is set for Tuesday in Calendar.",
    "I'll remind you that the API key rotates monthly.", "We have noted three issues in the log.",
    "- Codex — made a note in the PR about the cron.", "I've written a short note for you: Dear Anna, the release moves to Friday.",
    "As noted in the log, the deploy finished at 09:12.", "Just a reminder that the build is green.",
    "You noted that the deploy failed yesterday.", "It should be noted that the cache is cold.",
    "Your reminder settings are unchanged.", "I haven't set a reminder; say 'remind me at 5 pm to …'.",
    "The meeting is at 5.30 pm, version v1.2 shipped.", "I can set a reminder if you say 'remind me at 5 pm to …'.",
    "I've noted that the plist stays.", "Reminders are delivered by Telegram.", "I'll notify the Guild when the review lands.",
]


@pytest.mark.parametrize("sentence", UNSUPPORTED)
def test_claims_and_later_promises_are_unsupported(sentence):
    assert unsupported(sentence.split(". ", 1)[-1])
    out, removed = enforce(sentence)
    assert removed >= 1 and out.endswith(NOTICE)
    assert out == NOTICE or out == "Done.\n\n" + NOTICE          # "Done. I've saved that…" keeps "Done."


@pytest.mark.parametrize("reply", ORDINARY)
def test_history_negatives_and_remarks_are_left_alone(reply):
    assert enforce(reply) == (reply, 0)


def test_numbers_and_versions_never_split_a_sentence():
    out, removed = enforce("Ship v1.2 at 5.30 pm. I'll remind you at 5.15 pm.")
    assert removed == 1 and out == "Ship v1.2 at 5.30 pm.\n\n" + NOTICE


def test_lists_keep_their_lines():
    reply = "Plan:\n- Ship Monday.\n- I'll ping you at 9.\n- Review Tuesday."
    out, removed = enforce(reply)
    assert removed == 1 and out == "Plan:\n- Ship Monday.\n- Review Tuesday.\n\n" + NOTICE


def test_one_notice_however_many_claims():
    out, removed = enforce("Noted. I'll remind you at 5. Reminder set.")
    assert removed == 3 and out == NOTICE and out.count("No note or reminder") == 1


# ── Codex review of PR #342: negation must govern the claim; quoted text is not a claim ──

@pytest.mark.parametrize("claim", [
    "I saved a note so you will not forget.",          # the negation is after the claim, in another clause
    "Your note is saved, no problem.",
    "I can't send email, but I'll remind you at 5.",    # the limitation is in its own clause
    "Noted, I'll remind you at 5.",
])
def test_a_later_or_unrelated_negation_does_not_excuse_a_claim(claim):
    assert unsupported(claim) and enforce(claim) == (NOTICE, 1)


@pytest.mark.parametrize("ordinary", [
    'Here is a draft: "I will remind you tomorrow."',     # quoted, drafted content
    "She wrote “Reminder set for 5.”",
    "I cannot promise that I will remind you tomorrow.",  # a negation governing the promise
    "I won't remind you, sorry.",
    "No reminder is set yet.",
    "I haven't set a reminder; say 'remind me at 5 pm to …'.",
])
def test_drafts_and_governed_limitations_are_left_alone(ordinary):
    assert not unsupported(ordinary) and enforce(ordinary) == (ordinary, 0)


@pytest.mark.parametrize('reply',[
 'The brief has been written to `/tmp/brief.md` and is ready for download.',
 'Your download package is ready.',
 'I exported it to /tmp/brief.md.',
])
def test_file_claims_need_delivery_receipt(reply):
 from domains.cos.claim_truth import enforce_file_claims,FILE_NOTICE
 guarded,count=enforce_file_claims(reply)
 assert count and guarded.endswith(FILE_NOTICE)

@pytest.mark.parametrize('reply',[
 'No file is ready for download.', 'I have not written it to /tmp/brief.md.',
 'Here is the brief inline.', 'You can save this text yourself.',
])
def test_file_limitations_unchanged(reply):
 from domains.cos.claim_truth import enforce_file_claims
 assert enforce_file_claims(reply)==(reply,0)


def test_technical_file_location_is_not_a_delivery_claim():
 from domains.cos.claim_truth import enforce_file_claims
 reply='Logs are written to /opt/minimoi/agent_logs.'
 assert enforce_file_claims(reply)==(reply,0)
