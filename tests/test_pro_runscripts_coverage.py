from __future__ import annotations

from pathlib import Path

from supervisor.swe_bench_official_oracle import (
    preflight_swe_bench_pro_run_scripts,
)


def _write_instance_scripts(root: Path, instance_id: str) -> None:
    instance_dir = root / instance_id
    instance_dir.mkdir(parents=True)
    (instance_dir / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (instance_dir / "parser.py").write_text("import json\n", encoding="utf-8")


def test_missing_instance_scripts_reported(tmp_path):
    scripts_dir = tmp_path / "run_scripts"
    _write_instance_scripts(scripts_dir, "instance_present")
    (scripts_dir / "instance_parser_only").mkdir()
    (scripts_dir / "instance_parser_only" / "parser.py").write_text(
        "import json\n",
        encoding="utf-8",
    )

    report = preflight_swe_bench_pro_run_scripts(
        [
            "instance_present",
            "instance_missing",
            "instance_parser_only",
        ],
        scripts_dir=scripts_dir,
    )

    assert report["ok"] is False
    assert report["reason"] == (
        "pro_script_missing:"
        "instance_missing(parser.py,run_script.sh);"
        "instance_parser_only(run_script.sh)"
    )
    assert report["missing_instance_ids"] == [
        "instance_missing",
        "instance_parser_only",
    ]


def test_present_instance_scripts_resolve(tmp_path):
    scripts_dir = tmp_path / "run_scripts"
    _write_instance_scripts(scripts_dir, "instance_present")

    report = preflight_swe_bench_pro_run_scripts(
        ["instance_present"],
        scripts_dir=scripts_dir,
    )

    assert report["ok"] is True
    assert report["missing"] == []
    resolved = report["resolved"][0]
    assert resolved["instance_id"] == "instance_present"
    assert resolved["run_script"].endswith("instance_present/run_script.sh")
    assert resolved["parser"].endswith("instance_present/parser.py")
