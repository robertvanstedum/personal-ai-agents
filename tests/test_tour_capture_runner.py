from scripts.tools.tour_capture.runner import _retain_last_capture_diagnostic


def test_postprocessing_failure_retains_last_raw_capture(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    raw = tmp_path / "raw.png"
    raw.write_bytes(b"raw capture")

    artifacts = _retain_last_capture_diagnostic(diagnostics, [(1, {}, raw)])

    assert artifacts == ["last-captured.png"]
    assert (diagnostics / "last-captured.png").read_bytes() == b"raw capture"
