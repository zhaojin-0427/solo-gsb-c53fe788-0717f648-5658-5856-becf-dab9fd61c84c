"""极限规模性能试算：60 个基础故障、120 个门、恰好 8 个展开后重复 ID。

结构：
* s0..s7 为 8 个“共享”基础故障，共同位于 AND 门 SHARED 下；
  SHARED 被一条 67 级的门链反复引用，并由顶层门直接引用——
  展开后 s0..s7 各自重复（重复 ID 恰好 8 个，满足上限）。
* u0..u51 为 52 个唯一基础故障，用 51 个 AND/OR 门做二分归约树。
* 总门数 = 1(SHARED) + 51(归约树) + 67(复用链) + 1(TOP) = 120。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fault_tree import analyze, compute_multiplicities, normalize_payload  # noqa: E402


def build():
    shared = [f"s{i}" for i in range(8)]
    unique = [f"u{i}" for i in range(52)]
    basics = [{"id": i, "probability": "0.000123"} for i in shared + unique]

    gates = [{"id": "SHARED", "type": "AND", "inputs": shared}]

    # 52 个唯一叶子的二分归约树（51 个门）。
    layer = list(unique)
    idx = 0
    while len(layer) > 1:
        nxt = []
        for a, b in zip(layer[::2], layer[1::2]):
            name = f"H{idx}"
            idx += 1
            gates.append({"id": name, "type": "AND" if idx % 2 else "OR",
                          "inputs": [a, b]})
            nxt.append(name)
        if len(layer) % 2:
            nxt.append(layer[-1])
        layer = nxt
    uroot = layer[0]

    # 反复引用 SHARED 的无环门链（67 个门）。
    gates.append({"id": "C0", "type": "OR", "inputs": ["SHARED", uroot]})
    for i in range(1, 67):
        gates.append({"id": f"C{i}", "type": "AND",
                      "inputs": [f"C{i-1}", "SHARED"]})

    # 第 120 个门：顶层。
    gates.append({"id": "TOP", "type": "OR", "inputs": ["C66", "SHARED"]})
    return {"top_event": "TOP", "basic_events": basics, "gates": gates}


def main():
    payload = build()
    model = normalize_payload(payload)
    mult = compute_multiplicities(model["top"], model["probs"], model["gates"])
    repeated = [k for k, v in mult.items() if v > 1]
    print(f"basics={len(payload['basic_events'])} gates={len(payload['gates'])} "
          f"repeated_ids={len(repeated)}")
    assert len(payload["basic_events"]) == 60
    assert len(payload["gates"]) == 120
    assert len(repeated) == 8

    # 多次运行取最大值，模拟“交互试算”。
    worst = 0.0
    result = None
    for _ in range(5):
        t0 = time.perf_counter()
        result = analyze(payload)
        worst = max(worst, time.perf_counter() - t0)
    print(f"worst of 5 runs: {worst * 1000:.1f} ms (limit 5000 ms)")
    print("top_event_probability =", result["top_event_probability"])
    print("minimum_cut_set size  =", len(result["minimum_cut_set"]))
    assert worst < 5.0, "超过 5 秒时限"
    print("PERF OK")


if __name__ == "__main__":
    main()
