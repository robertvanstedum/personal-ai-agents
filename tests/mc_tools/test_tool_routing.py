"""Every tool MC is offered reaches something that answers it. Found on Dev, 8 Nov: `minimoi_build_item` and the helper tools
passed their adapter tests but the service had no route to them, so each call crashed."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.mc_tools import service as S
from core.mc_tools.agent_contact import TOOLS as AGENT_TOOLS
from core.mc_tools.board_adapter import OPS as BOARD_OPS
from core.mc_tools.host_artifacts import TOOLS as ARTIFACT_TOOLS
from core.mc_tools.host_files import TOOLS as HOST_TOOLS
from core.mc_tools.tasks_adapter import TasksAdapter

ROOT = Path(__file__).resolve().parents[2]


def offered():
    data = json.loads((ROOT / "core/mc_tools/tools.json").read_text(encoding="utf-8"))
    return [t["name"] for t in (data if isinstance(data, list) else data["tools"])]


class Routing(unittest.TestCase):
    def test_every_offered_tool_has_a_route(self):
        routed = AGENT_TOOLS | ARTIFACT_TOOLS | HOST_TOOLS | set(BOARD_OPS) | S.TASK_ADAPTER_TOOLS | {"minimoi_clarify_action"}
        missing = [n for n in offered() if not (n in routed or n.startswith("minimoi_task_")
                                                or hasattr(S.ToolService, "_" + n.removeprefix("minimoi_")))]
        self.assertEqual(missing, [])

    def test_the_generic_github_read_routes_to_its_own_handler(self):
        self.assertIn("minimoi_github_get", offered())
        self.assertTrue(callable(getattr(S.ToolService, "_github_get", None)))

    def test_the_task_adapter_answers_each_tool_routed_to_it(self):
        adapter = TasksAdapter.__new__(TasksAdapter)
        seen = []
        # Every tool in TASK_ADAPTER_TOOLS, including the work-artifact pair added with work modes (0690fcf3).
        methods = ("build_item", "helper_ask", "helper_result", "helper_review", "work_read", "work_save")
        for method in methods:
            setattr(adapter, method, lambda host, args, caller, m=method: seen.append(m) or "ok")
        host = SimpleNamespace(_tier2=lambda caller: True)
        for name in S.TASK_ADAPTER_TOOLS:
            self.assertEqual(adapter.call(host, name, {}, None), "ok")
        self.assertEqual(sorted(seen), sorted(methods))


if __name__ == "__main__":
    unittest.main()
