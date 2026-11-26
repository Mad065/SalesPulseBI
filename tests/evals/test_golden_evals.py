"""Automated Golden Dataset evaluation gate for SalesPulseBI AI Copilot."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from salespulsebi.storage.models import CustomPanelSpec

GOLDEN_PATH = Path(__file__).parent / "golden_cases.jsonl"


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for frame in raw.split("\n\n"):
        if not frame.strip():
            continue
        ev = "message"
        data_str = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data_str += line[5:].strip()
        if data_str:
            events.append((ev, json.loads(data_str)))
    return events


def test_golden_dataset_eval_gate(client: TestClient) -> None:
    """Enforce 100% tool accuracy, schema validity, panel validity, and guardrail block rate."""
    seed = client.post("/api/v1/demo/seed").json()
    pid = seed[0]["id"] if isinstance(seed, list) else seed["projects"][0]["id"]

    cases = [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(cases) >= 6

    for case in cases:
        with client.stream(
            "POST",
            f"/api/v1/projects/{pid}/ai/chat/stream",
            json={"message": case["query"], "lang": "es"},
        ) as resp:
            assert resp.status_code == 200
            events = _parse_sse("".join(resp.iter_text()))

        done_events = [d for ev, d in events if ev == "done"]
        assert len(done_events) == 1, f"Missing done event in {case['id']}"
        done = done_events[0]

        if case["expect_blocked"]:
            assert done["status"] == "blocked", f"Injection was not blocked in {case['id']}"
            continue

        assert done["status"] == "ok", f"Case {case['id']} failed"
        assert done["grounded"] is True

        called_tools = [d["tool_name"] for ev, d in events if ev == "tool_call"]
        if case["expected_tool"]:
            assert case["expected_tool"] in called_tools, (
                f"Expected tool {case['expected_tool']} in {case['id']}, got {called_tools}"
            )

        if case["expect_panel"]:
            panel_events = [d["panel"] for ev, d in events if ev == "panel_spec"]
            assert len(panel_events) == 1
            # Validate panel strictly against Pydantic v2 CustomPanelSpec
            CustomPanelSpec.model_validate(panel_events[0])
