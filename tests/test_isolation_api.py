"""/api/isolate 的 HTTP 层测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _frag(fid: str, offset: int, payload: str, weight: int) -> dict:
    return {"id": fid, "offset": offset, "payload": payload, "weight": weight}


AMBIGUOUS_BODY = {
    "target_length": 2,
    "fragments": [
        _frag("A", 0, "00", 100),
        _frag("B", 0, "01", 100),
        _frag("Z", 1, "FF", 1),
    ],
}


def test_isolate_rank1_success(client: TestClient) -> None:
    resp = client.post(
        "/api/isolate", json={**AMBIGUOUS_BODY, "selected_hex": "00FF"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["selected_hex"] == "00FF"
    assert data["isolated_fragment_ids"] == ["B"]
    assert data["isolated_weight"] == 100
    # 重算后的最优值与见证。
    assert data["optimal"] == {"total_weight": 101, "fragment_count": 2}
    assert data["target_body"]["hex"] == "00FF"
    assert set(data["target_body"]["witness_fragment_ids"]) == {"A", "Z"}
    # 逐项反例。
    ces = data["counterexamples"]
    assert len(ces) == 1
    ce = ces[0]
    assert ce["fragment_id"] == "B"
    assert ce["restored_verdict"] == "AMBIGUOUS"
    assert ce["selected_hex"] == "00FF"
    assert ce["rival_hex"] == "01FF"
    assert "B" in ce["rival_witness_fragment_ids"]
    assert ce["optimal"] == {"total_weight": 101, "fragment_count": 2}
    # 被隔离片段的内容与权重原样返回, 未被改动。
    assert data["isolated_fragments"] == [
        {"id": "B", "offset": 0, "payload": "01", "weight": 100}
    ]


def test_isolate_rank2_success(client: TestClient) -> None:
    resp = client.post(
        "/api/isolate", json={**AMBIGUOUS_BODY, "selected_hex": "01ff"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["selected_hex"] == "01FF"
    assert data["isolated_fragment_ids"] == ["A"]
    assert data["target_body"]["hex"] == "01FF"
    assert data["counterexamples"][0]["rival_hex"] == "00FF"


def test_isolate_rejected_when_unique(client: TestClient) -> None:
    body = {
        "target_length": 4,
        "fragments": [
            _frag("A", 0, "1122", 10),
            _frag("B", 2, "2233", 20),
        ],
        "selected_hex": "11222233",
    }
    resp = client.post("/api/isolate", json=body)
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert any(e["type"] == "value_error.plan_context" for e in errors)
    loc = next(e["loc"] for e in errors if e["type"] == "value_error.plan_context")
    assert loc == ["body", "selected_hex"]


def test_isolate_rejected_when_impossible(client: TestClient) -> None:
    body = {
        "target_length": 3,
        "fragments": [
            _frag("A", 0, "11", 1),
            _frag("B", 2, "33", 1),
        ],
        "selected_hex": "110033",
    }
    resp = client.post("/api/isolate", json=body)
    assert resp.status_code == 422
    assert any(e["type"] == "value_error.plan_context" for e in resp.json()["detail"])


def test_isolate_rejects_non_candidate_hex(client: TestClient) -> None:
    resp = client.post(
        "/api/isolate", json={**AMBIGUOUS_BODY, "selected_hex": "02FF"}
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    cand = [e for e in errors if e["type"] == "value_error.candidate"]
    assert len(cand) == 1
    assert cand[0]["loc"] == ["body", "selected_hex"]


def test_isolate_rejects_wrong_length_hex(client: TestClient) -> None:
    resp = client.post(
        "/api/isolate", json={**AMBIGUOUS_BODY, "selected_hex": "00"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "selected_hex"]


def test_isolate_rejects_malformed_hex(client: TestClient) -> None:
    resp = client.post(
        "/api/isolate", json={**AMBIGUOUS_BODY, "selected_hex": "ZZFF"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "selected_hex"]


def test_isolate_rejects_missing_selected_hex(client: TestClient) -> None:
    resp = client.post("/api/isolate", json=AMBIGUOUS_BODY)
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert any(
        e["loc"] == ["body", "selected_hex"] and e["type"] == "missing"
        for e in errors
    )


def test_isolate_rejects_non_string_selected_hex(client: TestClient) -> None:
    resp = client.post(
        "/api/isolate", json={**AMBIGUOUS_BODY, "selected_hex": 255}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "selected_hex"]


def test_isolate_input_validation_still_locates_fields(client: TestClient) -> None:
    body = {
        "target_length": 1,
        "fragments": [
            _frag("A", 0, "ZZ", 1),
            _frag("A", 0, "00", 1),
        ],
        "selected_hex": "00",
    }
    resp = client.post("/api/isolate", json=body)
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "fragments", 0, "payload") in locs
    assert ("body", "fragments", 1, "id") in locs


def test_isolate_invalid_json_body(client: TestClient) -> None:
    resp = client.post(
        "/api/isolate",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["type"] == "parse_error"


def test_isolate_multi_step_plan_full_structure(client: TestClient) -> None:
    # 7 个位置两路等权 -> 需隔离 7 片, 每个隔离片段都带独立反例。
    frags = []
    for i in range(7):
        frags.append(_frag(f"lo{i}", i, "00", 1))
        frags.append(_frag(f"hi{i}", i, "01", 1))
    body = {"target_length": 7, "fragments": frags, "selected_hex": "00" * 7}
    resp = client.post("/api/isolate", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["isolated_fragment_ids"] == [f"hi{i}" for i in range(7)]
    assert data["isolated_weight"] == 7
    assert data["optimal"] == {"total_weight": 7, "fragment_count": 7}
    ces = data["counterexamples"]
    assert {c["fragment_id"] for c in ces} == {f"hi{i}" for i in range(7)}
    for c in ces:
        assert c["restored_verdict"] == "AMBIGUOUS"
        assert c["rival_hex"] != c["selected_hex"]
        # 竞争见证必须动员被恢复的那个片段。
        assert c["fragment_id"] in c["rival_witness_fragment_ids"]
        assert c["optimal"] == {"total_weight": 7, "fragment_count": 7}
