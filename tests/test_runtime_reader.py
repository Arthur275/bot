import json

from dashboard.runtime_reader import RuntimeSnapshotReader, json_read_status, jsonl_count, read_json, tail_jsonl


def test_runtime_reader_paths(tmp_path) -> None:
    reader = RuntimeSnapshotReader(bot_root=tmp_path / "bot", quant_root=tmp_path / "quant")

    assert reader.bot_runtime == tmp_path / "bot" / "runtime"
    assert reader.bot_scheduler_root == tmp_path / "bot" / "runtime" / "bot_runtime_scheduler"
    assert reader.quant_analysis_root == tmp_path / "quant" / "runtime" / "analysis"
    assert reader.kill_switch_path == tmp_path / "bot" / "runtime" / "controls" / "disable_real_execution.flag"


def test_json_reader_reports_invalid_json(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")

    assert read_json(path) == {}
    status = json_read_status(path)
    assert status["status"] == "invalid_json"
    assert status["path"] == str(path)


def test_tail_jsonl_skips_invalid_rows_and_counts_nonempty_lines(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"a": 1}) + "\nnot-json\n\n" + json.dumps({"b": 2}) + "\n",
        encoding="utf-8",
    )

    assert tail_jsonl(path, limit=10) == [{"a": 1}, {"b": 2}]
    assert jsonl_count(path) == 3
