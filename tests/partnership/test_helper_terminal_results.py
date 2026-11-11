"""Both lead paths report authoritative terminal state even with late artifacts."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core.mc_tools.tasks_adapter import TasksAdapter
from services.helper_seats.lead import view

@pytest.mark.parametrize("state", ["failed", "canceled", "rejected"])
@pytest.mark.parametrize("artifacts", [[], [{"digest": "late", "artifacts": [{"id": "untrusted"}]}]])
def test_terminal_helper_does_not_claim_pending_or_offer_late_result_for_acceptance(state, artifacts):
    client = Mock()
    task = {"task_id": "TK-test", "holder": "apprentice", "state": state,
            "reason": "stopped", "artifact_sets": artifacts}
    cos = view(client, task)
    mc = TasksAdapter._helper_answer(SimpleNamespace(client=client), task)
    assert cos["status"] == state and cos["accepted"] is False
    assert mc["code"] == "helper_" + state
    assert mc["data"]["artifact_sets"] == artifacts
    client.get.assert_not_called()
