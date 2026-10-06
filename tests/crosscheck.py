"""Cross-validate the engine against exhaustive enumeration on random trees."""

import itertools
import random
import sys
from fractions import Fraction

sys.path.insert(0, ".")

from app.engine import FaultTreeError, analyze, build_tree  # noqa: E402


def brute_force(top, basics, gates_spec):
    tree = build_tree(top, basics, gates_spec)
    ids = list(tree.probs)
    probs = tree.probs
    gates = tree.gates

    def evaluate(state):
        val = {}

        def v(node):
            if node in val:
                return val[node]
            if node in probs:
                val[node] = state[node]
            else:
                g = gates[node]
                c = [v(x) for x in g.inputs]
                if g.gtype == "AND":
                    val[node] = int(all(c))
                elif g.gtype == "OR":
                    val[node] = int(any(c))
                else:
                    val[node] = int(sum(c) >= g.k)
            return val[node]

        return v(top)

    total = Fraction(0)
    p0 = {b: Fraction(0) for b in ids}
    true_sets = []
    for bits in itertools.product((0, 1), repeat=len(ids)):
        state = dict(zip(ids, bits))
        w = Fraction(1)
        for b in ids:
            w *= probs[b] if state[b] else 1 - probs[b]
        f = evaluate(state)
        total += w * f
        if f:
            true_sets.append(frozenset(b for b in ids if state[b]))
            for b in ids:
                if state[b] == 0:
                    rest = Fraction(1)
                    for c in ids:
                        if c != b:
                            rest *= probs[c] if state[c] else 1 - probs[c]
                    p0[b] += rest

    dec = {b: total - p0[b] for b in ids}

    # minimum cut set = minimum-cardinality minimal true point
    if not true_sets:
        cut = []
    else:
        # a true point is a minimal true point if removing any one member
        # makes the formula false
        minimal = []
        for s in true_sets:
            ok = True
            for x in s:
                state = {b: int(b in s and b != x) for b in ids}
                if evaluate(state):
                    ok = False
                    break
            if ok:
                minimal.append(s)
        m = min(len(s) for s in minimal)
        cut = sorted(min(tuple(sorted(s)) for s in minimal if len(s) == m))

    return total, dec, cut


def random_tree(rng):
    nb = rng.randint(3, 10)
    ids = [f"b{i}" for i in range(nb)]
    probs = [rng.choice(["0", "1", "0.5", "0.25", "0.75", "0.1", "0.3", "0.000001", "1.000000"])
             for _ in ids]
    basics = [{"id": i, "probability": p} for i, p in zip(ids, probs)]

    gates = []
    available = list(ids)
    ng = rng.randint(1, 12)
    for gi in range(ng):
        gid = f"g{gi}"
        gtype = rng.choice(["AND", "OR", "KOFN", "and", "k_of_n"])
        n_in = rng.randint(1, min(4, len(available)))
        inputs = rng.sample(available, n_in)
        spec = {"id": gid, "type": gtype, "inputs": inputs}
        t = gtype.upper().replace("-", "_")
        if t in ("KOFN", "K_OF_N"):
            spec["type"] = "KOFN"
            spec["k"] = rng.randint(1, n_in)
        gates.append(spec)
        available.append(gid)

    # retry if repeated count exceeds the limit (should be rare with 10 ids)
    top = available[-1]
    try:
        build_tree(top, basics, gates)
    except FaultTreeError:
        return None
    return top, basics, gates


def main(seed, trials):
    rng = random.Random(seed)
    checked = 0
    while checked < trials:
        spec = random_tree(rng)
        if spec is None:
            continue
        top, basics, gates = spec
        res = analyze(top, basics, gates)
        total, dec, cut = brute_force(top, basics, gates)

        assert res["top_event_probability"] == f"{total.numerator}/{total.denominator}", (
            spec, res["top_event_probability"], total)
        for b in basics:
            bid = b["id"]
            assert res["probability_decreases"][bid] == (
                f"{dec[bid].numerator}/{dec[bid].denominator}"), (spec, bid, res, dec)
        assert res["minimum_cut_set"] == cut, (spec, res["minimum_cut_set"], cut)
        checked += 1
        print(f"trial {checked}/{trials} ok: P={res['top_event_probability']} cut={cut}")
    print(f"ALL {trials} TRIALS PASSED")


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    trials = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    main(seed, trials)
