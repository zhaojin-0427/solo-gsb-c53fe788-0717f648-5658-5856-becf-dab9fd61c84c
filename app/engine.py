"""Fault tree analysis engine for the backup cooling chain API.

Supported gates: AND, OR and KOFN (at-least-k of n).  The graph may reuse
gates and basic events freely as long as it stays acyclic.

Probabilities are exact rationals (``fractions.Fraction``).  A basic event id
occurring more than once in the *expanded* top event is a "repeated" event;
there can be at most 8 of them.  Conditioning on every assignment of those
repeated ids (at most 256 cases) turns the remaining structure into a read-once
formula, on which top-event probability, per-event probability decrease and the
minimum cut set are each evaluated by a single tree pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

MAX_BASICS = 60
MAX_GATES = 120
MAX_REPEATED = 8

_PROB_RE = re.compile(r"^(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$")


class FaultTreeError(ValueError):
    """Semantic validation error; mapped to HTTP 422 by the API layer."""


@dataclass(frozen=True)
class Gate:
    gid: str
    gtype: str  # "AND" | "OR" | "KOFN"
    inputs: tuple[str, ...]
    k: Optional[int]


@dataclass
class _Tree:
    top: str
    probs: dict[str, Fraction]
    gates: dict[str, Gate]
    order: list[str]
    counts: dict[str, int]
    repeated: list[str]


def parse_probability(raw: str) -> Fraction:
    if not isinstance(raw, str) or not _PROB_RE.match(raw):
        raise FaultTreeError(
            f"invalid probability {raw!r}: expected a string between 0 and 1 "
            "with at most 6 decimal places"
        )
    return Fraction(raw)


def build_tree(
    top: str,
    basics: list[dict[str, str]],
    gate_specs: list[dict[str, object]],
) -> _Tree:
    if not isinstance(top, str) or not top:
        raise FaultTreeError("top_event must reference an existing basic id or gate id")

    probs: dict[str, Fraction] = {}
    for b in basics:
        bid = b.get("id")
        if not isinstance(bid, str) or not bid:
            raise FaultTreeError("basic fault id must be a non-empty string")
        if bid in probs:
            raise FaultTreeError(f"duplicated node definition: {bid!r}")
        probs[bid] = parse_probability(b.get("probability"))

    gates: dict[str, Gate] = {}
    for spec in gate_specs:
        gid = spec.get("id")
        if not isinstance(gid, str) or not gid:
            raise FaultTreeError("gate id must be a non-empty string")
        if gid in gates or gid in probs:
            raise FaultTreeError(f"duplicated node definition: {gid!r}")
        gtype = str(spec.get("type", "")).upper().replace("-", "_")
        if gtype in ("K_OF_N", "KOUTOFN", "VOTING"):
            gtype = "KOFN"
        if gtype not in ("AND", "OR", "KOFN"):
            raise FaultTreeError(f"gate {gid!r} has unknown type {gtype!r}")
        raw_inputs = spec.get("inputs")
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise FaultTreeError(f"gate {gid!r} must have a non-empty inputs list")
        inputs = tuple(raw_inputs)
        if any(not isinstance(i, str) for i in inputs):
            raise FaultTreeError(f"gate {gid!r} inputs must be strings")
        if len(set(inputs)) != len(inputs):
            raise FaultTreeError(f"gate {gid!r} contains duplicated inputs")
        k = spec.get("k")
        if gtype == "KOFN":
            if not isinstance(k, int) or isinstance(k, bool):
                raise FaultTreeError(f"k_of_n gate {gid!r} requires an integer k")
            if k < 1 or k > len(inputs):
                raise FaultTreeError(
                    f"gate {gid!r}: k out of range, must be in 1..{len(inputs)}"
                )
        elif k is not None:
            raise FaultTreeError(f"gate {gid!r} is {gtype} but also provides k")
        gates[gid] = Gate(gid, gtype, inputs, k if gtype == "KOFN" else None)

    if len(probs) > MAX_BASICS:
        raise FaultTreeError(f"too many basic faults (max {MAX_BASICS})")
    if len(gates) > MAX_GATES:
        raise FaultTreeError(f"too many gates (max {MAX_GATES})")

    if top not in probs and top not in gates:
        raise FaultTreeError(f"top_event {top!r} is not a defined basic id or gate id")

    for gate in gates.values():
        for child in gate.inputs:
            if child not in probs and child not in gates:
                raise FaultTreeError(
                    f"gate {gate.gid!r} references undefined node {child!r}"
                )

    # Cycle check + topological ordering over the whole gate graph.
    state: dict[str, int] = {}  # 0 = visiting, 1 = done
    order: list[str] = []

    def visit(gid: str, path: list[str]) -> None:
        s = state.get(gid)
        if s == 0:
            cyc = path[path.index(gid):] + [gid]
            raise FaultTreeError("cycle detected: " + " -> ".join(cyc))
        if s == 1:
            return
        state[gid] = 0
        for child in gates[gid].inputs:
            if child in gates:
                visit(child, path + [gid])
        state[gid] = 1
        order.append(gid)

    for gid in gates:
        if state.get(gid) != 1:
            visit(gid, [])

    # Expansion multiplicity of basic ids reachable from the top event.
    memo: dict[str, dict[str, int]] = {}

    def mult(node: str) -> dict[str, int]:
        if node in memo:
            return memo[node]
        if node in probs:
            res = {node: 1}
        else:
            merged: dict[str, int] = {}
            for child in gates[node].inputs:
                for bid, n in mult(child).items():
                    merged[bid] = merged.get(bid, 0) + n
            res = merged
        memo[node] = res
        return res

    counts = {top: 1} if top in probs else mult(top)
    repeated = sorted(b for b, n in counts.items() if n >= 2)
    if len(repeated) > MAX_REPEATED:
        raise FaultTreeError(
            f"expanded top event has {len(repeated)} repeated basic ids "
            f"(max {MAX_REPEATED})"
        )

    return _Tree(top, probs, gates, order, counts, repeated)


def _ronly_sets(tree: _Tree) -> dict[str, frozenset[str]]:
    """For every gate: the set of repeated basic ids in its subtree when the
    subtree contains *only* repeated basics, otherwise an empty frozenset."""
    rset = set(tree.repeated)
    out: dict[str, frozenset[str]] = {}
    for node in tree.order:
        acc: set[str] = set()
        ok = True
        for c in tree.gates[node].inputs:
            if c in tree.probs:
                if c not in rset:
                    ok = False
                    break
                acc.add(c)
            else:
                sub = out.get(c)
                if not sub:
                    ok = False
                    break
                acc |= sub
        out[node] = frozenset(acc) if ok else frozenset()
    return out


@dataclass
class _Eval:
    """One conditioning assignment of the repeated variables."""

    tree: _Tree
    assignment: dict[str, int]
    ronly_val: dict[str, list[int]]
    ronly_ms: dict[str, list[Optional[tuple[str, ...]]]]
    mask: int
    support: dict[str, frozenset[str]]
    conv_cache: dict
    probs: dict[str, Fraction] = field(default_factory=dict)
    minsets: dict[str, Optional[tuple[str, ...]]] = field(default_factory=dict)
    # cached convolution distributions / thresholds of soft KOFN children
    kofn_dist: dict[str, tuple[tuple[Fraction, ...], int]] = field(default_factory=dict)
    # non-repeated leaf id -> (P(top|leaf=0), P(top|leaf=1))
    leaf_influence: dict[str, tuple[Fraction, Fraction]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Repeated basics and r-only gates are fixed 0/1 constants under this
        # mask; make their values available even when the forward pass reached
        # them only through the memoized table.
        for bid, val in self.assignment.items():
            self.probs[bid] = Fraction(val)
        for gid, col in self.ronly_val.items():
            self.probs[gid] = Fraction(col[self.mask])

    def _basic_value(self, bid: str) -> Fraction:
        if bid in self.assignment:
            return Fraction(self.assignment[bid])
        return self.tree.probs[bid]

    def _basic_minset(self, bid: str) -> Optional[tuple[str, ...]]:
        if bid in self.assignment:
            return () if self.assignment[bid] == 1 else None
        return (bid,)

    def run_forward(self) -> Fraction:
        t = self.tree
        table = self.ronly_val
        support = self.support

        def value(node: str) -> Fraction:
            if node in t.probs:
                v = self._basic_value(node)
                self.probs[node] = v
                return v
            if node in table:
                out = Fraction(table[node][self.mask])
                self.probs[node] = out
                return out
            g = t.gates[node]
            child_vals = [value(c) for c in g.inputs]
            if g.gtype == "AND":
                out = Fraction(1)
                for v in child_vals:
                    out *= v
                    if out == 0:
                        break
            elif g.gtype == "OR":
                out = Fraction(1)
                for v in child_vals:
                    out *= 1 - v
                    if out == 0:
                        break
                out = 1 - out
            else:
                soft_vals: list[Fraction] = []
                fixed_ones = 0
                for c, v in zip(g.inputs, child_vals):
                    if not support.get(c, frozenset()):
                        fixed_ones += int(v)
                    else:
                        soft_vals.append(v)
                key = (node, tuple(soft_vals))
                cached_conv = self.conv_cache.get(key)
                if cached_conv is not None:
                    dist = list(cached_conv)
                else:
                    dist = [Fraction(1)]
                    for v in soft_vals:
                        extra = [Fraction(0)] * (len(dist) + 1)
                        for i, d in enumerate(dist):
                            extra[i] += d * (1 - v)
                            extra[i + 1] += d * v
                        dist = extra
                    self.conv_cache[key] = tuple(dist)
                kk = g.k - fixed_ones
                if kk <= 0:
                    out = Fraction(1)
                elif kk > len(dist) - 1:
                    out = Fraction(0)
                else:
                    out = sum(dist[kk:], Fraction(0))
                if len(dist) > 1:
                    self.kofn_dist[node] = (tuple(dist), kk)
            self.probs[node] = out
            return out

        return value(t.top)

    def run_minset(self) -> Optional[tuple[str, ...]]:
        t = self.tree
        table = self.ronly_ms

        def mset(node: str) -> Optional[tuple[str, ...]]:
            if node in t.probs:
                m = self._basic_minset(node)
                self.minsets[node] = m
                return m
            if node in table:
                m = table[node][self.mask]
                self.minsets[node] = m
                return m
            g = t.gates[node]
            child_ms = [mset(c) for c in g.inputs]
            if g.gtype == "AND":
                if any(m is None for m in child_ms):
                    out: Optional[tuple[str, ...]] = None
                else:
                    out = tuple(sorted({x for m in child_ms for x in m}))
            elif g.gtype == "OR":
                out = None
                for m in child_ms:
                    if m is not None and (out is None or (len(m), m) < (len(out), out)):
                        out = m
            else:
                # None: child is impossible under this conditioning (fixed 0);
                # (): child always occurs (fixed 1); otherwise a soft option
                # with its own non-repeated variable set.
                ones = sum(1 for m in child_ms if m == ())
                opts = [m for m in child_ms if m is not None and m != ()]
                need = g.k - ones
                if need <= 0:
                    out = ()
                elif need > len(opts):
                    out = None
                else:
                    # Read-once conditioned formula: child variable sets are
                    # disjoint, so the cheapest `need` children win outright.
                    chosen = sorted(opts, key=lambda m: (len(m), m))[:need]
                    out = tuple(sorted({x for m in chosen for x in m}))
            self.minsets[node] = out
            return out

        return mset(t.top)

    def _node_value(self, node: str) -> Fraction:
        """Probability of any node under this conditioning mask."""
        if node in self.probs:
            return self.probs[node]
        if node in self.tree.probs:
            return self._basic_value(node)
        col = self.ronly_val.get(node)
        return Fraction(col[self.mask]) if col is not None else Fraction(0)

    def run_backward(self, support: dict[str, frozenset[str]]) -> None:
        """Propagate (a, b) = P(top | node forced to 0 / 1) down the tree.

        Only children carrying non-repeated variables need recursion; repeated
        variables are fixed by the conditioning assignment and enter each gate
        as 0/1 constants.
        """
        t = self.tree

        def rec(node: str, a: Fraction, b: Fraction) -> None:
            if node in t.probs:
                if node not in self.assignment:
                    self.leaf_influence[node] = (a, b)
                return
            g = t.gates[node]
            if g.gtype == "AND":
                for c in g.inputs:
                    if not support.get(c, frozenset()):
                        continue
                    other = Fraction(1)
                    for d in g.inputs:
                        if d != c:
                            other *= self._node_value(d)
                    rec(c, a, a + other * (b - a))
            elif g.gtype == "OR":
                for c in g.inputs:
                    if not support.get(c, frozenset()):
                        continue
                    other = Fraction(1)
                    for d in g.inputs:
                        if d != c:
                            other *= 1 - self._node_value(d)
                    rec(c, b + other * (a - b), b)
            else:
                soft = [c for c in g.inputs if support.get(c, frozenset())]
                cached = self.kofn_dist.get(node)
                if cached is None:
                    # No soft child carries an in-(0,1) random variable on
                    # this mask: influence is decided by 0/1 constants.
                    fixed_ones = sum(
                        1 for c in g.inputs
                        if not support.get(c, frozenset()) and self._node_value(c) == 1
                    )
                    for c in soft:
                        rest = fixed_ones + sum(
                            1 for d in soft if d != c and self._node_value(d) == 1
                        )
                        need = g.k - rest
                        if need <= 0:
                            rec(c, b, b)
                        elif need == 1:
                            rec(c, a, b)
                        else:
                            rec(c, a, a)
                    return
                dist_t, kk = cached
                dist = list(dist_t)
                if kk <= 0:
                    for c in soft:
                        rec(c, b, b)
                    return
                soft_probs = [self._node_value(c) for c in soft]
                excl_key = ("excl", node, tuple(soft_probs))
                exclusions = self.conv_cache.get(excl_key)
                if exclusions is None:
                    exclusions = _all_exclusions(dist, soft_probs)
                    self.conv_cache[excl_key] = exclusions
                for c, excl in zip(soft, exclusions):
                    tail0 = sum(excl[kk:], Fraction(0))
                    if 0 <= kk - 1 < len(excl):
                        tail1 = tail0 + excl[kk - 1]
                    else:
                        tail1 = tail0
                    rec(c, a + (b - a) * tail0, a + (b - a) * tail1)

        rec(t.top, Fraction(0), Fraction(1))


def _all_exclusions(
    dist: list[Fraction], probs: list[Fraction]
) -> list[list[Fraction]]:
    """For every i, divide the product polynomial ``dist`` by the i-th linear
    Bernoulli factor (q_i + p_i z).  One product build and one O(degree)
    synthetic division per factor."""
    out: list[list[Fraction]] = []
    for p in probs:
        m = len(dist) - 1
        if p == 0:
            out.append(list(dist[:-1]))
            continue
        if p == 1:
            out.append(list(dist[1:]))
            continue
        q = 1 - p
        excl: list[Fraction] = []
        for i in range(m):
            prev = excl[i - 1] if i else Fraction(0)
            excl.append((dist[i] - p * prev) / q)
        out.append(excl)
    return out


def _support_sets(tree: _Tree) -> dict[str, frozenset[str]]:
    """Non-repeated basic ids under each node (the read-once remainder)."""
    repeated = set(tree.repeated)
    memo: dict[str, frozenset[str]] = {}

    def sup(node: str) -> frozenset[str]:
        if node in memo:
            return memo[node]
        if node in tree.probs:
            res = frozenset() if node in repeated else frozenset((node,))
        else:
            res = frozenset()
            for c in tree.gates[node].inputs:
                res |= sup(c)
        memo[node] = res
        return res

    sup(tree.top)
    return memo


def _precompute_ronly(tree: _Tree) -> tuple[
    dict[str, list[int]], dict[str, list[Optional[tuple[str, ...]]]]
]:
    """Boolean value and conditioned minimal true set of every r-only gate
    for all 2^r conditioning masks (computed once, in topological order)."""
    r = tree.repeated
    rset = set(r)
    ronly = _ronly_sets(tree)
    nmasks = 1 << len(r)
    val_tbl: dict[str, list[int]] = {}
    ms_tbl: dict[str, list[Optional[tuple[str, ...]]]] = {}

    bval = [[0] * nmasks for _ in r]
    bms: list[list[Optional[tuple[str, ...]]]] = []
    for i, bid in enumerate(r):
        bms.append([() if (m >> i) & 1 else None for m in range(nmasks)])
        for m in range(nmasks):
            bval[i][m] = (m >> i) & 1

    for node, supp in ronly.items():
        if not supp:
            continue
        g = tree.gates[node]
        vcols: list[list[int]] = []
        mcols: list[list[Optional[tuple[str, ...]]]] = []
        for c in g.inputs:
            if c in rset:
                idx = r.index(c)
                vcols.append(bval[idx])
                mcols.append(bms[idx])
            else:
                vcols.append(val_tbl[c])
                mcols.append(ms_tbl[c])
        vals = [0] * nmasks
        msets: list[Optional[tuple[str, ...]]] = [None] * nmasks
        for m in range(nmasks):
            cv = [col[m] for col in vcols]
            cm = [col[m] for col in mcols]
            if g.gtype == "AND":
                v = int(all(cv))
                ms: Optional[tuple[str, ...]]
                ms = tuple(sorted({x for c in cm for x in c})) if all(
                    x is not None for x in cm
                ) else None
            elif g.gtype == "OR":
                v = int(any(cv))
                ms = None
                for x in cm:
                    if x is not None and (ms is None or (len(x), x) < (len(ms), ms)):
                        ms = x
            else:
                v = int(sum(cv) >= g.k)
                ones = sum(1 for x in cm if x == ())
                opts = [x for x in cm if x is not None and x != ()]
                need = g.k - ones
                if need <= 0:
                    ms = ()
                elif need > len(opts):
                    ms = None
                else:
                    chosen = sorted(opts, key=lambda x: (len(x), x))[:need]
                    ms = tuple(sorted({x for c in chosen for x in c}))
            vals[m] = v
            msets[m] = ms
        val_tbl[node] = vals
        ms_tbl[node] = msets
    return val_tbl, ms_tbl


def analyze(
    top: str,
    basics: list[dict[str, str]],
    gate_specs: list[dict[str, object]],
) -> dict[str, object]:
    tree = build_tree(top, basics, gate_specs)
    r = tree.repeated
    support = _support_sets(tree)
    ronly_val, ronly_ms = _precompute_ronly(tree)

    top_total = Fraction(0)
    # accumulated P(top | bid = 0), weighted by the other repeated variables
    p0_repeated: dict[str, Fraction] = {bid: Fraction(0) for bid in r}
    decrease_nonr: dict[str, Fraction] = {bid: Fraction(0) for bid in tree.probs}

    best_minset: Optional[tuple[str, ...]] = None
    best_key: Optional[tuple[int, tuple[str, ...]]] = None

    # KOFN convolution / exclusion polynomials depend only on the child
    # probabilities, which for a given gate are mask-independent whenever the
    # gate sits in the read-once remainder; share them across all masks.
    conv_cache: dict = {}

    n = len(r)
    for mask in range(1 << n):
        assignment = {bid: (mask >> i) & 1 for i, bid in enumerate(r)}

        def factor(i: int, bid: str) -> Fraction:
            p = tree.probs[bid]
            return p if assignment[bid] else 1 - p

        weight = Fraction(1)
        for i, bid in enumerate(r):
            weight *= factor(i, bid)

        ev = _Eval(tree, assignment, ronly_val, ronly_ms, mask, support, conv_cache)
        top_val = ev.run_forward()
        top_total += weight * top_val

        # The cut set is a purely structural question: does an assignment of
        # the non-repeated variables make the top event occur?  Evaluate it on
        # every mask, even when the probabilistic top value is 0.
        m = ev.run_minset()
        if m is not None:
            fixed_ones = tuple(
                sorted(bid for bid, val in assignment.items() if val == 1)
            )
            candidate = tuple(sorted(set(m) | set(fixed_ones)))
            key = (len(candidate), candidate)
            if best_key is None or key < best_key:
                best_key = key
                best_minset = candidate

        if top_val == 0:
            # With the top event impossible under this conditioning, every
            # influence contribution and repeated-id counterfactual term is 0.
            continue
        ev.run_backward(support)
        for bid in tree.probs:
            if bid in assignment:
                continue
            infl = ev.leaf_influence.get(bid)
            if infl is not None:
                # P(top) - P(top|bid=0) = p * (P(top|bid=1) - P(top|bid=0))
                decrease_nonr[bid] += (
                    weight * tree.probs[bid] * (infl[1] - infl[0])
                )

        # Counterfactual for each repeated id forced to 0: weight the term by
        # the probability of ALL OTHER repeated variables (works even at p = 1).
        for j, bid in enumerate(r):
            if assignment[bid] != 0:
                continue
            rest = Fraction(1)
            for i, other in enumerate(r):
                if i != j:
                    rest *= factor(i, other)
            p0_repeated[bid] += rest * top_val

    decreases: dict[str, str] = {}
    for bid in tree.probs:
        if bid in r:
            dec = top_total - p0_repeated[bid]
        elif bid in tree.counts:
            dec = decrease_nonr[bid]
        else:
            dec = Fraction(0)
        decreases[bid] = f"{dec.numerator}/{dec.denominator}"

    return {
        "top_event_probability": f"{top_total.numerator}/{top_total.denominator}",
        "probability_decreases": decreases,
        "minimum_cut_set": list(best_minset) if best_minset is not None else [],
    }
