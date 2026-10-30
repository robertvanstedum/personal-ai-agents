"""fakejudge (host runtime spec v0.5 §9.3): a deterministic stand-in for MC's judgment call. It asks the authority for a
reservation first (R7.5-c), exactly as MC must; only a reserved call "runs", and every call is recorded in
``judge.jsonl`` with its fixed proposal. It is not a provider: no model, no network, no key."""
import hashlib
import json

PROPOSAL = "fakejudge/1: no change since the last review"


class FakeJudge:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def review(self, items, trigger, *, by="mc"):
        answer = self.store.reserve_judgment(trigger=trigger, items=list(items), by=by)
        if answer["state"] != "reserved":
            return answer
        outcome = {"kind": "no_change", "digest": hashlib.sha256(PROPOSAL.encode()).hexdigest()}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"reservation_id": answer["reservation_id"], "items": answer["items"],
                                 "outcome": outcome}, sort_keys=True) + "\n")
        recorded = self.store.record_judgment(answer["reservation_id"], usage=answer["amount"], outcome=outcome)
        return {**answer, "recorded": recorded}

    def calls(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return [json.loads(x) for x in fh if x.strip()]
        except FileNotFoundError:
            return []
