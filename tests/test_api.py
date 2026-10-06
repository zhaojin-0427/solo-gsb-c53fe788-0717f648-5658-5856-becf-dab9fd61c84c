"""HTTP-layer and validation tests for POST /analyses."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


COOLING_CHAIN = {
    "top_event": "BACKUP_COOLING_FAIL",
    "basic_faults": [
        {"id": "POWER_LOSS", "probability": "0.001"},
        {"id": "GEN_FAIL", "probability": "0.01"},
        {"id": "SWG_A_OPEN", "probability": "0.005"},
        {"id": "SWG_B_OPEN", "probability": "0.005"},
        {"id": "CHILLER_A_FAIL", "probability": "0.02"},
        {"id": "PUMP_A_FAIL", "probability": "0.02"},
        {"id": "VLV_A_FAIL", "probability": "0.01"},
        {"id": "CHILLER_B_FAIL", "probability": "0.02"},
        {"id": "PUMP_B_FAIL", "probability": "0.02"},
        {"id": "VLV_B_FAIL", "probability": "0.01"},
        {"id": "TCU_A_FAIL", "probability": "0.01"},
        {"id": "TCU_B_FAIL", "probability": "0.01"},
        {"id": "TCU_C_FAIL", "probability": "0.01"},
        {"id": "CTRL_BUS_FAIL", "probability": "0.003"},
        {"id": "SENSOR_BIAS", "probability": "0.004"},
    ],
    "gates": [
        {"id": "POWER_FAIL", "type": "AND",
         "inputs": ["POWER_LOSS", "GEN_FAIL"]},
        {"id": "TRAIN_A_FAIL", "type": "AND",
         "inputs": ["SWG_A_OPEN", "CHILLER_A_FAIL", "PUMP_A_FAIL", "VLV_A_FAIL"]},
        {"id": "TRAIN_B_FAIL", "type": "AND",
         "inputs": ["SWG_B_OPEN", "CHILLER_B_FAIL", "PUMP_B_FAIL", "VLV_B_FAIL"]},
        {"id": "TCU_2_OF_3", "type": "KOFN", "k": 2,
         "inputs": ["TCU_A_FAIL", "TCU_B_FAIL", "TCU_C_FAIL"]},
        {"id": "CTRL_FAIL", "type": "AND",
         "inputs": ["CTRL_BUS_FAIL", "SENSOR_BIAS"]},
        {"id": "BACKUP_COOLING_FAIL", "type": "OR",
         "inputs": ["POWER_FAIL", "TRAIN_A_FAIL", "TRAIN_B_FAIL",
                    "TCU_2_OF_3", "CTRL_FAIL"]},
    ],
}


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_cooling_chain_response_shape_and_fractions():
    r = client.post("/analyses", json=COOLING_CHAIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "top_event_probability", "probability_decreases", "minimum_cut_set"
    }
    # every submitted basic id has a reduced-fraction decrease string
    for b in COOLING_CHAIN["basic_faults"]:
        fr = body["probability_decreases"][b["id"]]
        num, den = fr.split("/")
        assert int(num) >= 0 and int(den) >= 1
    # minimum cut set of size 2 with lexicographically smallest id list is
    # the control branch {CTRL_BUS_FAIL, SENSOR_BIAS}
    assert body["minimum_cut_set"] == ["CTRL_BUS_FAIL", "SENSOR_BIAS"]


def test_shared_basic_id_same_fault():
    # x appears in both branches of an AND top: x AND (y OR x) == x
    # (the same id in different branches is literally the same fault)
    req = {
        "top_event": "G",
        "basic_faults": [
            {"id": "x", "probability": "0.5"},
            {"id": "y", "probability": "0.5"},
        ],
        "gates": [
            {"id": "G", "type": "AND", "inputs": ["x", "H"]},
            {"id": "H", "type": "OR", "inputs": ["y", "x"]},
        ],
    }
    body = client.post("/analyses", json=req).json()
    assert body["top_event_probability"] == "1/2"
    assert body["probability_decreases"]["x"] == "1/2"
    assert body["probability_decreases"]["y"] == "0/1"
    assert body["minimum_cut_set"] == ["x"]


def test_reused_gate():
    # top = (a AND g) OR (b AND g), g = c OR d -> g AND (a OR b)
    req = {
        "top_event": "TOP",
        "basic_faults": [
            {"id": "a", "probability": "0.5"},
            {"id": "b", "probability": "0.5"},
            {"id": "c", "probability": "0.5"},
            {"id": "d", "probability": "0.5"},
        ],
        "gates": [
            {"id": "g", "type": "OR", "inputs": ["c", "d"]},
            {"id": "TOP", "type": "AND", "inputs": ["g", "U"]},
            {"id": "U", "type": "OR", "inputs": ["a", "b"]},
        ],
    }
    body = client.post("/analyses", json=req).json()
    # P(g)=3/4, P(U)=3/4 independent -> 9/16; min cut {a,c}
    assert body["top_event_probability"] == "9/16"
    assert body["minimum_cut_set"] == ["a", "c"]
    assert body["probability_decreases"]["c"] == "3/16"  # 3/4 * 1/4


def test_basic_top_event():
    req = {"top_event": "x", "basic_faults": [{"id": "x", "probability": "0.2"}], "gates": []}
    body = client.post("/analyses", json=req).json()
    assert body["top_event_probability"] == "1/5"
    assert body["probability_decreases"] == {"x": "1/5"}
    assert body["minimum_cut_set"] == ["x"]


@pytest.mark.parametrize("payload,msg", [
    # cycle
    ({"top_event": "G1", "basic_faults": [{"id": "x", "probability": "0.5"}],
      "gates": [{"id": "G1", "type": "OR", "inputs": ["x", "G2"]},
                {"id": "G2", "type": "OR", "inputs": ["G1"]}]}, "cycle"),
    # dangling reference
    ({"top_event": "G1", "basic_faults": [{"id": "x", "probability": "0.5"}],
      "gates": [{"id": "G1", "type": "OR", "inputs": ["ghost"]}]}, "undefined"),
    # empty gate
    ({"top_event": "G1", "basic_faults": [{"id": "x", "probability": "0.5"}],
      "gates": [{"id": "G1", "type": "OR", "inputs": []}]}, "at least 1"),
    # duplicate basic definition
    ({"top_event": "G1",
      "basic_faults": [{"id": "x", "probability": "0.5"},
                       {"id": "x", "probability": "0.2"}],
      "gates": [{"id": "G1", "type": "OR", "inputs": ["x"]}]}, "duplicat"),
    # gate id collides with basic id
    ({"top_event": "x1",
      "basic_faults": [{"id": "x1", "probability": "0.5"}],
      "gates": [{"id": "x1", "type": "OR", "inputs": ["x1"]}]}, "duplicat"),
    # duplicate gate inputs
    ({"top_event": "G1", "basic_faults": [{"id": "x", "probability": "0.5"}],
      "gates": [{"id": "G1", "type": "OR", "inputs": ["x", "x"]}]}, "duplicat"),
    # k out of range
    ({"top_event": "G1", "basic_faults": [{"id": "x", "probability": "0.5"}],
      "gates": [{"id": "G1", "type": "KOFN", "k": 0, "inputs": ["x"]}]}, "out of range"),
    ({"top_event": "G1", "basic_faults": [{"id": "x", "probability": "0.5"}],
      "gates": [{"id": "G1", "type": "KOFN", "k": 2, "inputs": ["x"]}]}, "out of range"),
])
def test_semantic_422(payload, msg):
    r = client.post("/analyses", json=payload)
    assert r.status_code == 422, (r.status_code, r.text)
    detail = r.json()["detail"]
    text = detail if isinstance(detail, str) else str(detail)
    assert msg in text


def test_bad_probability_422():
    for bad in ["0.1234567", "1.000001", "1.2", "-0.1", "", "abc", "0.", ".5", "00.5"]:
        req = {"top_event": "x", "basic_faults": [{"id": "x", "probability": bad}], "gates": []}
        r = client.post("/analyses", json=req)
        assert r.status_code == 422, (bad, r.status_code)


def test_valid_probability_boundaries_accepted():
    for good in ["0", "1", "0.000000", "1.000000", "0.000001", "0.999999", "0.5"]:
        req = {"top_event": "x", "basic_faults": [{"id": "x", "probability": good}], "gates": []}
        assert client.post("/analyses", json=req).status_code == 200, good


def test_too_many_repeated_ids_422():
    # 9 distinct basics each used twice in the expanded top event
    ids = [f"x{i}" for i in range(9)]
    req = {
        "top_event": "TOP",
        "basic_faults": [{"id": i, "probability": "0.5"} for i in ids],
        "gates": [
            {"id": "L", "type": "AND", "inputs": ids},
            {"id": "R", "type": "AND", "inputs": ids},
            {"id": "TOP", "type": "OR", "inputs": ["L", "R"]},
        ],
    }
    r = client.post("/analyses", json=req)
    assert r.status_code == 422
    assert "repeated" in r.json()["detail"]


def test_lexicographic_tie_break():
    # top = (a AND b) OR (a AND c) OR (d AND e): only one 2-cut {a,b}-style...
    # Build a case with two equal-size cuts: (x1 OR x2) AND (y1 OR y2)
    req = {
        "top_event": "TOP",
        "basic_faults": [
            {"id": "x1", "probability": "0.5"},
            {"id": "x2", "probability": "0.5"},
            {"id": "y1", "probability": "0.5"},
            {"id": "y2", "probability": "0.5"},
        ],
        "gates": [
            {"id": "X", "type": "OR", "inputs": ["x1", "x2"]},
            {"id": "Y", "type": "OR", "inputs": ["y1", "y2"]},
            {"id": "TOP", "type": "AND", "inputs": ["X", "Y"]},
        ],
    }
    body = client.post("/analyses", json=req).json()
    assert body["minimum_cut_set"] == ["x1", "y1"]
