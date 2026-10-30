"""Disposable configuration and synthetic diagnostics; no provider calls."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from domains.curator.data_paths import data_directory


def test_absent_data_root_uses_repository_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CURATOR_DATA_DIR", raising=False)
    assert data_directory(tmp_path) == tmp_path / "data" / "curator"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_empty_data_root_is_rejected_before_io(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURATOR_DATA_DIR", value)
    with pytest.raises(ValueError, match="nonempty path"):
        data_directory(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", ["relative", "/synthetic/curator", "directory with spaces"])
def test_explicit_data_root_is_preserved(monkeypatch, tmp_path, value):
    monkeypatch.setenv("CURATOR_DATA_DIR", value)
    assert data_directory(tmp_path) == Path(value)


@pytest.mark.parametrize("content", [[], [SimpleNamespace(text="PRIVATE_SENTINEL_INVALID_JSON")],
                                    [SimpleNamespace()]])
def test_metadata_parse_failure_does_not_log_response(monkeypatch, capsys, content):
    # Compile the real function without importing the adapter, whose cold import
    # loads host configuration and creates its configured data directory.
    import ast
    import json
    source = Path(__file__).resolve().parents[1] / "domains/curator/curator_feedback.py"
    tree = ast.parse(source.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "extract_metadata")
    namespace = {
        "json": json,
        "get_anthropic_api_key": lambda: "synthetic",
        "Anthropic": lambda **kw: SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(content=content))),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    monkeypatch.setattr("domains.curator.feedback_prompts.metadata_prompt", lambda *a: "synthetic")
    result = namespace["extract_metadata"]({}, "synthetic", "interested")
    assert result["content_type"] == ["unknown"]
    output = capsys.readouterr().out
    assert "using fallback" in output
    assert "PRIVATE_SENTINEL" not in output
    assert "Response was" not in output
