"""输入校验（422）与边界条件测试。"""
import pytest

from app.fault_tree import FaultTreeError, analyze


def base(**over):
    p = {
        "top_event": "G",
        "basic_events": [
            {"id": "A", "probability": "0.1"},
            {"id": "B", "probability": "0.2"},
        ],
        "gates": [{"id": "G", "type": "AND", "inputs": ["A", "B"]}],
    }
    p.update(over)
    return p


def expect422(payload):
    with pytest.raises(FaultTreeError):
        analyze(payload)


# ---- 概率字符串 -------------------------------------------------------------


def test_probability_formats():
    ok = ["0", "0.0", "1", "1.0", "0.000001", "0.999999", "0.5", "1.000000", "0.123456"]
    for text in ok:
        p = base()
        p["basic_events"][0]["probability"] = text
        analyze(p)

    bad = ["", "0.0000001", "1.1", "-0.1", "0.", ".5", "1.0000001",
           "0/1", "0.5e-3", " 0.1", "0.1 ", "abc", "2", "100%", None, 0.1]
    for text in bad:
        p = base()
        p["basic_events"][0]["probability"] = text
        expect422(p)


# ---- 结构错误 ---------------------------------------------------------------


def test_cycle():
    expect422({
        "top_event": "G1",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [
            {"id": "G1", "type": "AND", "inputs": ["A", "G2"]},
            {"id": "G2", "type": "OR", "inputs": ["G1"]},
        ],
    })


def test_self_loop_gate():
    expect422({
        "top_event": "G1",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [{"id": "G1", "type": "AND", "inputs": ["G1"]}],
    })


def test_dangling_reference():
    expect422({
        "top_event": "G",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [{"id": "G", "type": "AND", "inputs": ["A", "X"]}],
    })


def test_dangling_top():
    expect422({
        "top_event": "Nope",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [],
    })


def test_empty_gate():
    expect422({
        "top_event": "G",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [{"id": "G", "type": "AND", "inputs": []}],
    })


def test_duplicate_gate_definition():
    expect422({
        "top_event": "G",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [
            {"id": "G", "type": "AND", "inputs": ["A"]},
            {"id": "G", "type": "OR", "inputs": ["A"]},
        ],
    })


def test_duplicate_basic_definition():
    expect422({
        "top_event": "G",
        "basic_events": [
            {"id": "A", "probability": "0.1"},
            {"id": "A", "probability": "0.2"},
        ],
        "gates": [{"id": "G", "type": "OR", "inputs": ["A"]}],
    })


def test_duplicate_gate_input():
    expect422({
        "top_event": "G",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [{"id": "G", "type": "AND", "inputs": ["A", "A"]}],
    })


def test_gate_reuse_is_allowed():
    # 同一个门被多个父门引用是合法复用，不是重复输入（不同父门各自引用一次）。
    res = analyze({
        "top_event": "TOP",
        "basic_events": [
            {"id": "A", "probability": "0.1"},
            {"id": "B", "probability": "0.2"},
            {"id": "C", "probability": "0.3"},
        ],
        "gates": [
            {"id": "SHARED", "type": "AND", "inputs": ["A", "B"]},
            {"id": "L", "type": "AND", "inputs": ["SHARED", "C"]},
            {"id": "R", "type": "OR", "inputs": ["SHARED", "C"]},
            {"id": "TOP", "type": "OR", "inputs": ["L", "R"]},
        ],
    })
    assert res["top_event_probability"]


def test_id_collision_basic_and_gate():
    expect422({
        "top_event": "A",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [{"id": "A", "type": "OR", "inputs": ["A"]}],
    })


# ---- KOFN -------------------------------------------------------------------


def test_k_out_of_range():
    common = {
        "basic_events": [
            {"id": "A", "probability": "0.1"},
            {"id": "B", "probability": "0.2"},
        ],
    }
    for k in (0, 3, -1):
        expect422({
            **common,
            "top_event": "G",
            "gates": [{"id": "G", "type": "KOFN", "k": k, "inputs": ["A", "B"]}],
        })


def test_kofn_k1_equals_or():
    basics = [{"id": c, "probability": "0.13"} for c in "ABC"]
    gates = [{"id": "G", "type": "KOFN", "k": 1, "inputs": ["A", "B", "C"]}]
    res = analyze({"top_event": "G", "basic_events": basics, "gates": gates})
    assert res["minimum_cut_set"] == ["A"]


def test_kofn_kn_equals_and():
    basics = [{"id": c, "probability": "0.13"} for c in "ABC"]
    gates = [{"id": "G", "type": "KOFN", "k": 3, "inputs": ["A", "B", "C"]}]
    res = analyze({"top_event": "G", "basic_events": basics, "gates": gates})
    assert res["minimum_cut_set"] == ["A", "B", "C"]


# ---- 规模约束 ---------------------------------------------------------------


def test_too_many_basics():
    expect422({
        "top_event": "G",
        "basic_events": [{"id": f"B{i}", "probability": "0.1"} for i in range(61)],
        "gates": [{"id": "G", "type": "OR",
                   "inputs": [f"B{i}" for i in range(61)]}],
    })


def test_too_many_gates():
    # 121 个门，彼此串联无环。
    gates = [{"id": f"G{i}", "type": "OR",
              "inputs": [f"G{i+1}"]} for i in range(120)]
    gates.append({"id": "G120", "type": "OR", "inputs": ["A"]})
    expect422({
        "top_event": "G0",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": gates,
    })


def test_too_many_repeated_basic_ids():
    # 让 9 个基础故障各出现 2 次：门 H_i = AND(x_i, y_i) 形式不行，
    # 需要“同一叶子跨两条支路”。构造两个 AND 大支路，各自包含 9 个
    # 不同基础故障，再由 OR 汇总——每个叶子在展开树中出现 1 次，不重复；
    # 正确做法是让一个共享 AND 门被两个父门引用，其下 9 个叶子各重复。
    ids = [f"B{i}" for i in range(9)]
    expect422({
        "top_event": "TOP",
        "basic_events": [{"id": i, "probability": "0.1"} for i in ids + ["C", "D"]],
        "gates": [
            {"id": "SHARED", "type": "AND", "inputs": ids},
            {"id": "L", "type": "AND", "inputs": ["SHARED", "C"]},
            {"id": "R", "type": "AND", "inputs": ["SHARED", "D"]},
            {"id": "TOP", "type": "OR", "inputs": ["L", "R"]},
        ],
    })


# ---- 退化与边界 -------------------------------------------------------------


def test_top_is_basic_event():
    res = analyze({
        "top_event": "A",
        "basic_events": [
            {"id": "A", "probability": "0.25"},
            {"id": "B", "probability": "0.5"},
        ],
        "gates": [],
    })
    assert res["top_event_probability"] == "1/4"
    assert res["probability_reductions"]["A"] == "1/4"
    assert res["probability_reductions"]["B"] == "0/1"
    assert res["minimum_cut_set"] == ["A"]


def test_zero_and_one_probabilities():
    res = analyze({
        "top_event": "G",
        "basic_events": [
            {"id": "A", "probability": "0"},
            {"id": "B", "probability": "1"},
        ],
        "gates": [{"id": "G", "type": "OR", "inputs": ["A", "B"]}],
    })
    assert res["top_event_probability"] == "1/1"
    # B 必然发生；把 B 置为不发生后只剩 A（概率 0），降幅为 1。
    assert res["probability_reductions"]["B"] == "1/1"
    assert res["probability_reductions"]["A"] == "0/1"


def test_unreachable_basics_get_zero_reduction():
    res = analyze({
        "top_event": "G",
        "basic_events": [
            {"id": "A", "probability": "0.1"},
            {"id": "Z", "probability": "0.9"},
        ],
        "gates": [{"id": "G", "type": "OR", "inputs": ["A"]}],
    })
    assert res["probability_reductions"]["Z"] == "0/1"
    assert "Z" not in res["minimum_cut_set"]


def test_reductions_fraction_is_reduced():
    res = analyze({
        "top_event": "G",
        "basic_events": [
            {"id": "A", "probability": "0.5"},
            {"id": "B", "probability": "0.5"},
        ],
        "gates": [{"id": "G", "type": "AND", "inputs": ["A", "B"]}],
    })
    assert res["top_event_probability"] == "1/4"
    # 把任一输入置为不发生，AND 顶事件恒不发生，故降幅均为 1/4（已约分）。
    assert res["probability_reductions"]["A"] == "1/4"
    assert res["probability_reductions"]["B"] == "1/4"


def test_bad_gate_type():
    expect422({
        "top_event": "G",
        "basic_events": [{"id": "A", "probability": "0.1"}],
        "gates": [{"id": "G", "type": "XOR", "inputs": ["A"]}],
    })
