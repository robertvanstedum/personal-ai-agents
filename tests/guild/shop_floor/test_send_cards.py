"""#618: the four 15 Nov phrasings become send cards. yes sends the exact text. edit reissues the card."""
import tempfile
import unittest

from minimoi_portal.guild_ui.send_cards import Store, parse_reply, propose


EARLIER = ({"author_kind": "owner", "text": "Ask Grok to finish the voice fix."},)


class ProposeTests(unittest.TestCase):
    def test_the_four_phrasings_each_make_a_card(self):
        two = propose("can you send a message to grok on this fix part and to claude code on the deployment part")
        self.assertEqual(two, [
            {"to": "grok", "text": "this fix part"},
            {"to": "claude-code", "text": "the deployment part"},
        ])
        it = propose("Tell it now to finish build asap", EARLIER)
        self.assertEqual(it, [{"to": "grok", "text": "finish build asap"}])
        per = propose("per Robert", ({"author_kind": "owner", "text": "Tell grok to finish build"},))
        self.assertEqual(per, [{"to": "grok", "text": "finish build"}])
        one = propose("Tell grok to finish build")
        self.assertEqual(one, [{"to": "grok", "text": "finish build"}])

    def test_a_question_keeps_the_card_and_drops_the_question(self):
        got = propose("Tell grok to finish build. Does that sound right?")
        self.assertEqual(got, [{"to": "grok", "text": "finish build"}])

    def test_quoted_negated_and_ambiguous_it_do_not_propose(self):
        self.assertIsNone(propose('"Tell grok to finish build"'))
        self.assertIsNone(propose("Do not tell grok to finish build"))
        self.assertIsNone(propose("Tell it now to finish build asap"))
        self.assertIsNone(propose("Tell it now to finish build asap", (
            {"author_kind": "owner", "text": "Ask Grok and Claude Code."},)))
        self.assertIsNone(propose("does that sound right?"))

    def test_yes_sends_only_the_card_text_and_edit_reissues(self):
        root = tempfile.mkdtemp()
        store = Store(root)
        [card] = store.issue("c-abc", propose("Tell grok to finish build"))
        self.assertEqual(parse_reply(f"yes {card['id']}"), ("yes", card["id"], ""))
        self.assertEqual(parse_reply(f"yes {card['id']}, but wait")[0], "unclear")
        self.assertIsNone(parse_reply("yes"))
        edited = store.replace("c-abc", card["id"], "finish #618 only")
        self.assertEqual(edited["text"], "finish #618 only")
        self.assertNotEqual(edited["id"], card["id"])
        self.assertEqual(store.get("c-abc", card["id"])["state"], "edited")
        self.assertEqual(store.get("c-abc", edited["id"])["state"], "pending")
        self.assertIsNone(store.replace("c-abc", card["id"], "again"))


class SpokenCodeTests(unittest.TestCase):
    """Staging, 26 Nov: a spoken "Yes, S U 4 W S" sent nothing. The id as speech writes it matches; only that exact id."""

    def test_the_id_as_typed_or_spoken_matches_exactly(self):
        for said in ("yes S-U4WS", "Yes, S U 4 W S", "yes, S-U4WS", "yes s u 4 w s", "Yes S-U-4-W-S.", "YES. S.U.4.W.S!",
                     "yes SU4WS", "yes S - U4WS"):
            self.assertEqual(parse_reply(said), ("yes", "S-U4WS", ""), said)
        self.assertEqual(parse_reply("No, S U 4 W S"), ("no", "S-U4WS", ""))
        self.assertEqual(parse_reply("edit S U 4 W S: new words"), ("edit", "S-U4WS", "new words"))

    def test_nothing_near_the_id_approves(self):
        for said in ("yes S U 4 W", "yes S-U4W", "yes S U 4 W S 7", "yes S-U4WSX", "yes U4WS", "yes S-U4WS, but wait",
                     "yes S O 4 W S", "yesS-U4WS", "yes please S-U4WS", "maybe S-U4WS"):
            parsed = parse_reply(said)
            self.assertTrue(parsed is None or parsed[0] == "unclear", (said, parsed))

    def test_a_second_yes_finds_nothing_waiting(self):
        import tempfile
        store = Store(tempfile.mkdtemp())
        [card] = store.issue("c-abc", propose("Tell grok to finish build"))
        self.assertEqual(parse_reply("Yes, S " + " ".join(card["id"][2:]))[1], card["id"])
        store.mark("c-abc", card["id"], "sent")
        self.assertEqual(store.get("c-abc", card["id"])["state"], "sent")      # api.py: not pending, so nothing is sent again


if __name__ == "__main__":
    unittest.main()
