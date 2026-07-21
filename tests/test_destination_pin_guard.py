"""Guard: the pinned North-Star destination cannot drift silently.

Enforces, without any supervisor imports (pure stdlib + yaml):
  1. The NORTH-STAR.md fenced YAML block's sha256 matches the pin registry.
  2. The amendment chain is contiguous (each amendment's old_sha256 equals the
     previous pin's sha256; the latest amendment's new_sha256 equals the
     current pin).
  3. The YAML block is well-formed: unique clause ids, each clause carries a
     non-empty predicate name, and the destination text is non-empty.

The normalization here MUST match the "Normalization rule" section of
docs/program/NORTH-STAR.md: lines strictly between the ```yaml fence and the
closing ``` fence, each right-stripped, joined with "\n", one trailing "\n",
UTF-8. If you are editing this file to make a destination change pass, stop:
the correct path is an amendment record per the amendment protocol.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
NORTH_STAR = REPO_ROOT / "docs" / "program" / "NORTH-STAR.md"
PINS = REPO_ROOT / "docs" / "program" / "pins.json"
AMENDMENT_GLOB = "NORTH-STAR-amendment-*.json"


def _normalized_yaml_block(markdown_text: str) -> str:
    lines = markdown_text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "```yaml")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "```")
    except StopIteration as exc:  # pragma: no cover - structural failure
        raise AssertionError("NORTH-STAR.md must contain exactly one fenced ```yaml block") from exc
    return "\n".join(l.rstrip() for l in lines[start + 1 : end]) + "\n"


def _current_pin() -> dict:
    registry = json.loads(PINS.read_text(encoding="utf-8"))
    entries = [p for p in registry["pins"] if p["artifact"] == "docs/program/NORTH-STAR.md"]
    assert len(entries) == 1, "exactly one NORTH-STAR pin entry is required"
    return entries[0]


def _amendments() -> list[dict]:
    records = []
    for path in sorted((REPO_ROOT / "docs" / "program").glob(AMENDMENT_GLOB)):
        match = re.search(r"amendment-(\d+)\.json$", path.name)
        assert match, f"amendment file name malformed: {path.name}"
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record.get("amendment") == int(match.group(1)), (
            f"{path.name}: 'amendment' field must equal the filename number"
        )
        for field in ("old_sha256", "new_sha256", "reason", "approver", "date"):
            assert record.get(field), f"{path.name}: missing required field {field!r}"
        records.append(record)
    return sorted(records, key=lambda r: r["amendment"])


def test_destination_block_hash_matches_pin() -> None:
    block = _normalized_yaml_block(NORTH_STAR.read_text(encoding="utf-8"))
    actual = hashlib.sha256(block.encode("utf-8")).hexdigest()
    pinned = _current_pin()["sha256"]
    assert actual == pinned, (
        "NORTH-STAR destination block does not match its pin. If this change is "
        "intended, follow the amendment protocol in NORTH-STAR.md (add an "
        f"amendment record and update pins.json). actual={actual} pinned={pinned}"
    )


def test_amendment_chain_is_contiguous() -> None:
    amendments = _amendments()
    pin = _current_pin()
    if not amendments:
        assert pin.get("amendment_ref") in (None, ""), (
            "pin references an amendment but no amendment files exist"
        )
        return
    numbers = [r["amendment"] for r in amendments]
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"amendment numbering must be contiguous from 1, got {numbers}"
    )
    for previous, current in zip(amendments, amendments[1:]):
        assert current["old_sha256"] == previous["new_sha256"], (
            f"amendment-{current['amendment']} old_sha256 does not chain from "
            f"amendment-{previous['amendment']} new_sha256"
        )
    assert amendments[-1]["new_sha256"] == pin["sha256"], (
        "latest amendment's new_sha256 must equal the current pin"
    )


def test_destination_yaml_is_well_formed() -> None:
    block = _normalized_yaml_block(NORTH_STAR.read_text(encoding="utf-8"))
    data = yaml.safe_load(block)
    destination = data["destination"]
    assert str(destination.get("text", "")).strip(), "destination text must be non-empty"
    clauses = destination["clauses"]
    ids = [c["id"] for c in clauses]
    assert len(ids) == len(set(ids)), f"clause ids must be unique, got {ids}"
    assert ids == sorted(ids, key=lambda x: int(x[1:])), "clause ids must be ordered C1..Cn"
    for clause in clauses:
        assert str(clause.get("predicate", "")).strip(), (
            f"clause {clause.get('id')} must name a predicate"
        )
        assert str(clause.get("meaning", "")).strip(), (
            f"clause {clause.get('id')} must state its meaning"
        )
