"""Worst-case performance probe: 60 basics, 120 gates, 8 repeated ids."""

import sys
import time

sys.path.insert(0, ".")

from app.engine import analyze

# 8 shared basics f0..f7 feed 120 different AND gates (distinct subsets,
# each becomes a 0/1 constant per conditioning); the top k-of-n gate also
# collects 52 unique leaf basics u0..u51.  This maximises the 256-conditioning
# workload for every gate type.
shared = [f"f{i}" for i in range(8)]
unique = [f"u{i}" for i in range(52)]
basics = [{"id": b, "probability": "0.500000"} for b in shared + unique]

gates = []
combos = []
# 119 distinct non-empty subsets of the 8 shared ids
from itertools import combinations

for size in range(1, 9):
    for comb in combinations(shared, size):
        combos.append(list(comb))
        if len(combos) == 119:
            break
    if len(combos) == 119:
        break
assert len(combos) == 119

for i, comb in enumerate(combos):
    gtype = ["AND", "OR", "KOFN"][i % 3]
    spec = {"id": f"g{i}", "type": gtype, "inputs": comb}
    if gtype == "KOFN":
        spec["k"] = (i % len(comb)) + 1
    gates.append(spec)

gate_ids = [f"g{i}" for i in range(119)]
gates.append({"id": "TOP", "type": "KOFN", "k": 90, "inputs": gate_ids + unique})

t0 = time.perf_counter()
res = analyze("TOP", basics, gates)
dt = time.perf_counter() - t0
print(f"worst case: {dt:.3f}s")
print("top =", res["top_event_probability"])
print("cut size =", len(res["minimum_cut_set"]))
assert dt < 5.0, f"too slow: {dt:.3f}s"

# A realistic read-once-heavy case (no repeated ids): single enumeration
basics2 = [{"id": f"b{i}", "probability": "0.001"} for i in range(60)]
gates2 = []
layer = [f"b{i}" for i in range(60)]
nid = 0
while len(layer) > 1:
    nxt = []
    for i in range(0, len(layer) - 1, 2):
        nid += 1
        gates2.append({"id": f"h{nid}", "type": "KOFN", "k": 2,
                       "inputs": [layer[i], layer[i + 1]]})
        nxt.append(f"h{nid}")
    if len(layer) % 2:
        nxt.append(layer[-1])
    layer = nxt
t0 = time.perf_counter()
res2 = analyze(layer[0], basics2, gates2)
dt2 = time.perf_counter() - t0
print(f"60-fault read-once tree ({len(gates2)} gates): {dt2:.3f}s")
print("top =", res2["top_event_probability"], "cut =", res2["minimum_cut_set"])
assert dt2 < 5.0
print("PERF OK")
