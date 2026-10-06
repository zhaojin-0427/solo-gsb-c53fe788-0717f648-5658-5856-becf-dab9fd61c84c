"""对引擎结果与朴素全状态枚举做随机一致性校验（小图）。"""
import itertools
import random
from fractions import Fraction

from app.fault_tree import analyze


def brute_force(payload):
    ids = [b["id"] for b in payload["basic_events"]]
    probs = {b["id"]: Fraction(b["probability"]) for b in payload["basic_events"]}
    gates = {g["id"]: g for g in payload["gates"]}
    top = payload["top_event"]

    def evaluate(v, node=None):
        node = top if node is None else node
        if node in probs:
            return v[node]
        g = gates[node]
        vals = [evaluate(v, r) for r in g["inputs"]]
        if g["type"] == "AND":
            return int(all(vals))
        if g["type"] == "OR":
            return int(any(vals))
        return int(sum(vals) >= g["k"])

    def prob_do(t=None):
        # do(t=0)：只在“其余变量”上枚举，权重也只含其余变量；
        # t 被强制为 0，其因子不重复计入。
        free = [i for i in ids if i != t] if t is not None else list(ids)
        total = Fraction(0)
        for bits in itertools.product([0, 1], repeat=len(free)):
            v = {i: 0 for i in ids}
            v.update(dict(zip(free, bits)))
            w = Fraction(1)
            for i in free:
                w *= probs[i] if v[i] == 1 else (1 - probs[i])
            total += w * evaluate(v)
        return total

    p = prob_do(None)
    reds = {t: p - prob_do(t) for t in ids}

    # 最小割集：故障数最少；同数时在“升序 ID 列表”的字符串字典序下最小。
    # 字典序直接用 Python 元组比较（与引擎一致），在所有同大小可行集合中取 min。
    best = None
    for r in range(len(ids) + 1):
        cands = []
        for comb in itertools.combinations(range(len(ids)), r):
            v = {i: 0 for i in ids}
            for idx in comb:
                v[ids[idx]] = 1
            if evaluate(v):
                cands.append(tuple(sorted(ids[idx] for idx in comb)))
        if cands:
            best = list(min(cands))
            break
    return p, reds, best


def check(payload):
    res = analyze(payload)
    p, reds, best = brute_force(payload)
    assert res["top_event_probability"] == f"{p.numerator}/{p.denominator}", (
        res["top_event_probability"], p)
    for t, red in reds.items():
        assert red >= 0
        assert Fraction(res["probability_reductions"][t]) == red, (t, res, red)
    assert res["minimum_cut_set"] == best, (res["minimum_cut_set"], best)


# 固定的冷却链例子
COOLING = {
    "top_event": "G_top",
    "basic_events": [
        {"id": "Pow", "probability": "0.005"},
        {"id": "P1", "probability": "0.01"},
        {"id": "P2", "probability": "0.02"},
        {"id": "P3", "probability": "0.04"},
        {"id": "CTS", "probability": "0.001"},
        {"id": "HXS", "probability": "0.002"},
        {"id": "E3", "probability": "0.03"},
        {"id": "E7", "probability": "0.07"},
    ],
    "gates": [
        {"id": "G_pumps", "type": "KOFN", "k": 2, "inputs": ["P1", "P2", "P3"]},
        {"id": "G_branchA", "type": "AND",
         "inputs": ["Pow", "G_pumps", "CTS", "E3"]},
        {"id": "G_branchB", "type": "AND",
         "inputs": ["Pow", "G_pumps", "HXS", "E7", "E3"]},
        {"id": "G_top", "type": "OR", "inputs": ["G_branchA", "G_branchB"]},
    ],
}


def random_tree(n_basics, n_gates, shared_depth=3, seed=0):
    """生成带门复用（共享变量）的随机无环故障树，基础数 <= 12 以便暴力枚举。"""
    rng = random.Random(seed)
    ids = [f"b{i}" for i in range(n_basics)]
    probs = [f"{rng.randint(0, 1000) / 1000:.3f}" for _ in ids]
    basics = [{"id": i, "probability": p} for i, p in zip(ids, probs)]
    gates = []
    built = list(ids)
    for gi in range(n_gates):
        gid = f"g{gi}"
        gtype = rng.choice(["AND", "OR", "KOFN"])
        k_inputs = rng.randint(2, min(4, len(built)))
        inputs = rng.sample(built, k_inputs)
        gate = {"id": gid, "type": gtype, "inputs": inputs}
        if gtype == "KOFN":
            gate["k"] = rng.randint(1, k_inputs)
        gates.append(gate)
        built.append(gid)
    return {"top_event": built[-1], "basic_events": basics, "gates": gates}


def test_cooling_example():
    check(COOLING)


def shared_reuse_tree(seed=0):
    """构造含 8 个共享叶子（一个被多处复用的 AND 门）+ 唯一叶子的深树。"""
    rng = random.Random(seed)
    shared = [f"s{i}" for i in range(8)]
    unique = [f"u{i}" for i in range(4)]
    all_ids = shared + unique
    basics = [{"id": i, "probability": f"{rng.randint(1, 999) / 1000:.3f}"}
              for i in all_ids]
    gates = [
        # 8 个共享叶子同在一个 AND 门下，该门被多处复用
        {"id": "SHARED", "type": "AND", "inputs": shared},
        {"id": "L1", "type": "KOFN", "k": 2, "inputs": ["SHARED", "u0", "u1"]},
        {"id": "L2", "type": "OR", "inputs": ["SHARED", "u2"]},
        {"id": "L3", "type": "AND", "inputs": ["L1", "u3"]},
        {"id": "TOP", "type": "OR", "inputs": ["L3", "L2", "SHARED"]},
    ]
    return {"top_event": "TOP", "basic_events": basics, "gates": gates}


def test_shared_reuse_trees():
    for seed in range(40):
        check(shared_reuse_tree(seed))


def test_random_trees():
    for seed in range(120):
        rng = random.Random(seed)
        n_b = rng.randint(4, 11)
        n_g = random.Random(seed + 999).randint(3, 9)
        payload = random_tree(n_b, n_g, seed=seed)
        # 展开后重复 ID 可能超过 8，引擎会拒绝；这种情况跳过。
        try:
            check(payload)
        except Exception as e:
            from app.fault_tree import FaultTreeError
            if isinstance(e, FaultTreeError) and "重复" in str(e):
                continue
            raise
