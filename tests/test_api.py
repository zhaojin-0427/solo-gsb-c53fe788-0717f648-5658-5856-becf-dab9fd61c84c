"""HTTP 层（FastAPI TestClient）测试。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

COOLING = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "cooling_chain.json").read_text()
)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_analyses_cooling_example_matches_contract():
    r = client.post("/analyses", json=COOLING)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"top_event_probability", "probability_reductions",
                         "minimum_cut_set"}
    # 与 README 中记录的响应逐字一致。
    assert body == {
        "top_event_probability": "29579367/125000000000000000",
        "probability_reductions": {
            "Pow": "29579367/125000000000000000",
            "P1": "12481467/125000000000000000",
            "P2": "21030417/125000000000000000",
            "P3": "6326223/31250000000000000",
            "CTS": "25946367/125000000000000000",
            "HXS": "3629367/125000000000000000",
            "E3": "29579367/125000000000000000",
            "E7": "3629367/125000000000000000",
        },
        "minimum_cut_set": ["CTS", "E3", "P1", "P2", "Pow"],
    }
    # 所有分数字符串均为最简形式。
    from fractions import Fraction
    for key, value in body["probability_reductions"].items():
        assert value == f"{Fraction(value).numerator}/{Fraction(value).denominator}"
    assert body["top_event_probability"] == (
        f"{Fraction(body['top_event_probability']).numerator}/"
        f"{Fraction(body['top_event_probability']).denominator}"
    )


def test_422_on_cycle():
    r = client.post("/analyses", json={
        "top_event": "G1",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [
            {"id": "G1", "type": "AND", "inputs": ["A", "G2"]},
            {"id": "G2", "type": "OR", "inputs": ["G1"]},
        ],
    })
    assert r.status_code == 422
    assert "循环" in r.json()["detail"]


def test_422_on_malformed_json():
    r = client.post("/analyses", content="{not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_422_on_non_object_body():
    r = client.post("/analyses", json=[1, 2, 3])
    assert r.status_code == 422


def test_openapi_available():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["paths"]["/analyses"]["post"] is not None
