from __future__ import annotations

import json

from supervisor.run_timeline import build_run_timeline


def test_run_timeline_filters_monitor_noise_and_extracts_pr_status_card():
    timeline = build_run_timeline(
        run={
            "run_id": "run-pr-54",
            "session_id": "sess-pr-54",
            "task": "Ship Claude SDK invocation",
            "status": "running",
            "rollout_path": "/tmp/rollout.jsonl",
            "started_at": 1779223000,
            "ended_at": None,
        },
        events=[
            {"id": 100, "kind": "state.stalled", "prev": "healthy"},
            {
                "id": 1001,
                "kind": "event_msg",
                "timestamp": "2026-05-19T20:32:00.000Z",
                "payload": {
                    "type": "agent_message",
                    "phase": "commentary",
                    "message": (
                        "frontend-smoke in progress. If any check failed, "
                        "notify Sam."
                    ),
                },
            },
            {
                "id": 101,
                "kind": "response_item",
                "timestamp": "2026-05-19T20:41:16.995Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-ci",
                    "output": "Output:\n" + json.dumps({
                        "headRefOid": "b07b510bd97787f34f1cd8020845c7c7d2bf5e4e",
                        "mergeable": "MERGEABLE",
                        "number": 54,
                        "state": "OPEN",
                        "statusCheckRollup": [
                            {
                                "__typename": "CheckRun",
                                "name": "backend-smoke",
                                "status": "COMPLETED",
                                "conclusion": "SUCCESS",
                            },
                            {
                                "__typename": "CheckRun",
                                "name": "frontend-smoke",
                                "status": "COMPLETED",
                                "conclusion": "SUCCESS",
                            },
                            {
                                "__typename": "CheckRun",
                                "name": "cassette-freshness",
                                "status": "COMPLETED",
                                "conclusion": "SUCCESS",
                            },
                            {
                                "__typename": "CheckRun",
                                "name": "cross-model-review",
                                "status": "COMPLETED",
                                "conclusion": "SKIPPED",
                            },
                        ],
                    }),
                },
            },
            {
                "id": 102,
                "kind": "response_item",
                "timestamp": "2026-05-19T20:41:45.964Z",
                "payload": {
                    "type": "message",
                    "phase": "final_answer",
                    "content": [{
                        "type": "output_text",
                        "text": (
                            "Merged PR #54.\n\n"
                            "- Approved SHA: `b07b510bd97787f34f1cd8020845c7c7d2bf5e4e`\n"
                            "- Merge commit: `9cb0f4a3e153f98ee4af1c8d0562b1df2d5ce8aa`\n"
                            "- PR state: `MERGED`"
                        ),
                    }],
                },
            },
            {"id": 103, "kind": "state.healthy", "prev": "stalled"},
        ],
    )

    assert [event["event_id"] for event in timeline["events"]] == [1001, 101, 102]
    assert timeline["monitor_state"] == {
        "event_id": 103,
        "state": "healthy",
        "previous": "stalled",
    }
    assert timeline["facts"]["primary_pr"] == "54"
    assert timeline["facts"]["approval_sha"] == "b07b510bd97787f34f1cd8020845c7c7d2bf5e4e"
    assert timeline["facts"]["merge_commit_sha"] == "9cb0f4a3e153f98ee4af1c8d0562b1df2d5ce8aa"
    assert timeline["facts"]["approval_token_candidate"] == (
        "[approved PR#54 b07b510bd97787f34f1cd8020845c7c7d2bf5e4e]"
    )
    assert timeline["facts"]["checks"] == [
        "backend-smoke passed",
        "frontend-smoke passed",
        "cassette-freshness passed",
        "cross-model-review skipped",
    ]
    assert timeline["outcome_card"]["headline"] == "PR #54 is merged."
    assert timeline["outcome_card"]["approval_token"] == (
        "[approved PR#54 b07b510bd97787f34f1cd8020845c7c7d2bf5e4e]"
    )
    assert "state.stalled" not in json.dumps(timeline)


def test_run_timeline_prefers_approved_or_head_sha_over_merge_commit_for_token():
    timeline = build_run_timeline(
        run={"run_id": "run-pr-54", "session_id": "sess-pr-54", "status": "running"},
        events=[
            {
                "id": 200,
                "kind": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": json.dumps({
                        "number": 54,
                        "state": "MERGED",
                        "mergeCommit": {
                            "oid": "9cb0f4a3e153f98ee4af1c8d0562b1df2d5ce8aa"
                        },
                    }),
                },
            },
            {
                "id": 201,
                "kind": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": (
                        "Merged PR #54.\n"
                        "Approved SHA: b07b510bd97787f34f1cd8020845c7c7d2bf5e4e\n"
                        "Merge commit: 9cb0f4a3e153f98ee4af1c8d0562b1df2d5ce8aa"
                    ),
                },
            },
        ],
    )

    assert timeline["facts"]["shas"][0] == "9cb0f4a3e153f98ee4af1c8d0562b1df2d5ce8aa"
    assert timeline["facts"]["approval_sha"] == "b07b510bd97787f34f1cd8020845c7c7d2bf5e4e"
    assert timeline["facts"]["approval_token_candidate"] == (
        "[approved PR#54 b07b510bd97787f34f1cd8020845c7c7d2bf5e4e]"
    )
