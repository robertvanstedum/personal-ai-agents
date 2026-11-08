"""MC's task tools: the schema the model is shown (tools.json) is what catalogue.validate enforces, and the adapter reads
only what that schema offers. Same check as CoS's cos_tasks (tests/cos/test_cos_tasks_e2e.py), which drifted on Dev."""
import re
from pathlib import Path

import pytest

from core.mc_tools import catalogue, tasks_adapter

SAMPLE = {"item_id": 8, "task_id": "TK-sample-0001", "child_id": "TK-sample-0002", "reviewed_child": "TK-sample-0003",
          "interpretation_id": "IN-sample-0001", "kind": "build", "holder": "codex", "outcome": "Some words.",
          "done_when": "Some words."}
SOURCE = Path(tasks_adapter.__file__).read_text()
METHOD = {"minimoi_task_list": "list", "minimoi_task_delegate": "delegate", "minimoi_task_submit": "submit",
          "minimoi_task_accept": "accept", "minimoi_task_adopt": "adopt", "minimoi_build_item": "build_item"}


def schema(name):
    return catalogue.TOOLS[name]["parameters"]


@pytest.mark.parametrize("name", tasks_adapter.TOOLS)
def test_the_fields_a_tool_marks_required_pass_the_real_validator(name):
    required = {k: SAMPLE[k] for k in schema(name)["required"]}
    assert catalogue.validate(name, required) == required


@pytest.mark.parametrize("name", [n for n in tasks_adapter.TOOLS if schema(n)["required"]])
def test_leaving_out_any_required_field_is_refused(name):
    for gone in schema(name)["required"]:
        with pytest.raises(catalogue.ArgumentsRefused):
            catalogue.validate(name, {k: SAMPLE[k] for k in schema(name)["required"] if k != gone})


@pytest.mark.parametrize("name", tasks_adapter.TOOLS)
def test_the_adapter_reads_only_fields_the_schema_offers(name):
    body = SOURCE.split(f"    def {METHOD[name]}(self, host, args, caller):", 1)[1].split("\n    def ", 1)[0]
    read = set(re.findall(r"""args(?:\.get\(|\[)["']([a-z_]+)["']""", body))
    read |= {k for line in re.findall(r"for key in \(([^)]*)\)", body) for k in re.findall(r'"([a-z_]+)"', line)}
    assert read <= set(schema(name)["properties"]), read
