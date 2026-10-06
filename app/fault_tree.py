"""故障树分析引擎。

支持的门类型：
- AND  : 全部输入发生时门发生；
- OR   : 任一输入发生时门发生；
- KOFN : n 个输入中恰好/至少 k 个发生时门发生（按“至少 k 个”计，
         这是工业故障树 k-out-of-n 投票门的标准语义）。

关键算法说明
===========
基础故障之间相互独立，但同一个基础故障 ID 可以通过“可复用的门”出现在
展开树的多条支路上，这些支路共享同一个随机变量，因此不能把各支路当成
独立叶子直接做乘积/求和。

本文档（以及 README）所称“展开顶事件后重复的基础 ID”，是指把 DAG 沿门
复用关系展开成树之后出现次数大于 1 的基础故障 ID。题目保证这类 ID 至多
8 个（记为“共享变量” S）。算法对 S 的 2^|S| <= 256 种 0/1 取值做枚举；
在每一种取值下：

* 所有可复用的门都成为确定常量；
* 余下未确定的子图是一棵“叶子互不相同”的树（唯一叶子与门均只有一个父
  节点），各子树彼此独立。

于是：

* 概率计算为树上的自底向上 DP；
* 顶事件对每个唯一叶子的偏导（即“该叶子不发生时顶事件概率的降幅”）由
  反向自动微分（伴随法）一次得到；
* 最小割集由树上的布尔 DP 得到，状态为 (常量门是否成立, 需要发生的叶子
  ID 有序列表)，跨 256 个取值再取“故障数最少、同数时升序 ID 列表字典序
  最小”的全局最优。

全程使用 :class:`fractions.Fraction` 做精确有理运算，输出统一约分。
"""

from __future__ import annotations

import re
import sys
from fractions import Fraction
from typing import Optional

# 门规模上限 120，逐层递归（含嵌套闭包）仍远小于默认 1000，这里再显式抬高，
# 避免不同运行环境下调低过递归上限导致深链失败。
sys.setrecursionlimit(max(sys.getrecursionlimit(), 4000))

# ---- 输入规模与格式约束 ----------------------------------------------------

MAX_BASICS = 60
MAX_GATES = 120
MAX_REPEATED = 8          # 展开后重复基础 ID 的上限
MAX_ID_LEN = 64
# 0~1 之间、最多六位小数的十进制小数字符串（不接受科学计数法、整数 0/1、
# 前后缀小数点等写法）
_PROB_RE = re.compile(r"^(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

AND = "AND"
OR = "OR"
KOFN = "KOFN"

GATE_TYPES = (AND, OR, KOFN)


class FaultTreeError(ValueError):
    """输入故障树不合法，HTTP 层映射为 422。"""


# ---- 归一化与校验 ----------------------------------------------------------


def _parse_probability(text: str) -> Fraction:
    if not isinstance(text, str) or not _PROB_RE.fullmatch(text):
        raise FaultTreeError(
            "概率必须是 0~1 之间、最多六位小数的十进制字符串，"
            f"例如 '0.000025'；收到 {text!r}"
        )
    return Fraction(text)


def _check_id(identifier: str, what: str) -> None:
    if not isinstance(identifier, str) or not identifier or len(identifier) > MAX_ID_LEN:
        raise FaultTreeError(f"{what} 必须是 1~{MAX_ID_LEN} 个字符的字符串")
    if not _ID_RE.fullmatch(identifier):
        raise FaultTreeError(f"{what} 只能包含字母、数字、下划线或连字符：{identifier!r}")


def normalize_payload(payload: dict) -> dict:
    """把原始请求字典归一化为内部表示，并做全部结构校验。

    返回::

        {
            "top": str,
            "probs": {基础ID: Fraction},
            "gates": {门ID: {"type": str, "inputs": [str], "k": int|None}},
        }
    """
    if not isinstance(payload, dict):
        raise FaultTreeError("请求体必须是 JSON 对象")

    top = payload.get("top_event")
    basics = payload.get("basic_events")
    gates = payload.get("gates", [])

    _check_id(top, "top_event")

    if not isinstance(basics, list) or not basics:
        raise FaultTreeError("basic_events 必须是非空数组（至多 60 个基础故障）")
    if len(basics) > MAX_BASICS:
        raise FaultTreeError(f"基础故障至多 {MAX_BASICS} 个，收到 {len(basics)} 个")

    probs: dict[str, Fraction] = {}
    for index, item in enumerate(basics):
        if not isinstance(item, dict):
            raise FaultTreeError(f"basic_events[{index}] 必须是对象")
        bid = item.get("id")
        raw_prob = item.get("probability")
        _check_id(bid, f"basic_events[{index}].id")
        if bid in probs:
            raise FaultTreeError(f"基础故障定义重复：{bid!r}")
        probs[bid] = _parse_probability(raw_prob)

    if not isinstance(gates, list):
        raise FaultTreeError("gates 必须是数组")
    if len(gates) > MAX_GATES:
        raise FaultTreeError(f"门至多 {MAX_GATES} 个，收到 {len(gates)} 个")

    normalized_gates: dict[str, dict] = {}
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            raise FaultTreeError(f"gates[{index}] 必须是对象")
        gid = gate.get("id")
        gtype = gate.get("type")
        inputs = gate.get("inputs")
        k = gate.get("k")
        _check_id(gid, f"gates[{index}].id")
        if gid in normalized_gates:
            raise FaultTreeError(f"门定义重复：{gid!r}")
        if gid in probs:
            raise FaultTreeError(f"门 ID 与基础故障 ID 冲突：{gid!r}")
        if gtype not in GATE_TYPES:
            raise FaultTreeError(
                f"门 {gid!r} 的 type 必须是 AND / OR / KOFN，收到 {gtype!r}"
            )
        if not isinstance(inputs, list) or not inputs:
            raise FaultTreeError(f"门 {gid!r} 的 inputs 必须是非空数组（不允许空门）")
        if len(inputs) != len(set(inputs)):
            raise FaultTreeError(f"门 {gid!r} 的输入存在重复节点引用")
        clean_inputs: list[str] = []
        for ref in inputs:
            _check_id(ref, f"门 {gid!r} 的输入")
            if ref == gid:
                raise FaultTreeError(f"门 {gid!r} 直接引用自身，存在自环")
            clean_inputs.append(ref)
        nk = len(clean_inputs)
        if gtype == KOFN:
            if not isinstance(k, int) or isinstance(k, bool):
                raise FaultTreeError(f"KOFN 门 {gid!r} 必须给出整数 k")
            if not 1 <= k <= nk:
                raise FaultTreeError(
                    f"KOFN 门 {gid!r} 的 k 越界：{k} 不在 [1, {nk}] 内"
                )
        else:
            # 非 KOFN 门忽略 k 字段；保持 None。
            k = None
        normalized_gates[gid] = {"type": gtype, "inputs": clean_inputs, "k": k}

    if top not in probs and top not in normalized_gates:
        raise FaultTreeError(f"顶事件 {top!r} 既不是基础故障也不是已定义的门（悬空引用）")

    # 校验所有引用均可解析（悬空引用），并做有环检测。
    _validate_references_and_acyclic(probs, normalized_gates)

    return {"top": top, "probs": probs, "gates": normalized_gates}


def _validate_references_and_acyclic(
    probs: dict[str, Fraction], gates: dict[str, dict]
) -> None:
    """整图扫描：悬空引用检测 + 有环检测（迭代式三色 DFS）。

    对每个门做拓扑遍历。即使某些门从顶事件不可达，其内部的环或悬空
    引用同样属于非法定义。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    # 先解析所有引用，保证环检测时“指向的是门还是基础故障”可直接判断。
    for gid, gate in gates.items():
        for ref in gate["inputs"]:
            if ref not in probs and ref not in gates:
                raise FaultTreeError(f"门 {gid!r} 引用了未定义节点 {ref!r}（悬空引用）")

    for start in gates:
        if color.get(start, WHITE) != WHITE:
            continue
        color[start] = GRAY
        # (节点, 下一个待处理输入下标)
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, idx = work[-1]
            inputs = gates[node]["inputs"]
            if idx < len(inputs):
                work[-1] = (node, idx + 1)
                ref = inputs[idx]
                if ref not in gates:
                    continue  # 基础故障叶子
                state = color.get(ref, WHITE)
                if state == GRAY:
                    raise FaultTreeError(f"故障树存在循环引用：门 {ref!r} 处于环中")
                if state == WHITE:
                    color[ref] = GRAY
                    work.append((ref, 0))
            else:
                color[node] = BLACK
                work.pop()


# ---- 展开倍数统计：找出“共享变量” -----------------------------------------


def compute_multiplicities(
    top: str, probs: dict[str, Fraction], gates: dict[str, dict]
) -> dict[str, int]:
    """统计每个基础 ID 在“展开成树”后的出现次数。

    对每个 DAG 节点维护一个 dict（基础ID -> 出现次数）。门复用导致同一
    门的 dict 被多个父节点累加，从而正确体现跨支路重复。
    """
    memo: dict[str, dict[str, int]] = {}

    def visit(node: str) -> dict[str, int]:
        if node in memo:
            return memo[node]
        if node in probs:
            memo[node] = {node: 1}
            return memo[node]
        gate = gates[node]
        acc: dict[str, int] = {}
        for ref in gate["inputs"]:
            for bid, cnt in visit(ref).items():
                acc[bid] = acc.get(bid, 0) + cnt
        memo[node] = acc
        return acc

    return visit(top)


# ---- 单个枚举取值下的前向概率 + 反向伴随 -----------------------------------


def _forward_gate(gate: dict, child_values: list[Fraction]) -> Fraction:
    gtype = gate["type"]
    if gtype == AND:
        result = Fraction(1)
        for v in child_values:
            result *= v
        return result
    if gtype == OR:
        result = Fraction(1)
        for v in child_values:
            result *= (1 - v)
        return 1 - result
    # KOFN：分布 DP，dist[j] = 恰有 j 个输入发生的概率
    dist = [Fraction(1)]
    k = gate["k"]
    for v in child_values:
        new_dist = [Fraction(0)] * (len(dist) + 1)
        for j, old in enumerate(dist):
            new_dist[j] += old * (1 - v)
            new_dist[j + 1] += old * v
        dist = new_dist
    return sum(dist[k:])


def _backward_gate(
    gate: dict,
    child_values: list[Fraction],
    adj_in: Fraction,
) -> list[Fraction]:
    """给定门的伴随值（门输出对顶事件概率的偏导），求每个输入的伴随。

    返回长度与输入相同的系数列表，对所有输入（包括当前取值下值为 0/1
    的常量门）都给出 ∂out/∂x_i 的代数系数。调用方负责决定：
    常量基础叶子（共享变量实例）不累加，常量门继续向上游传播。

    全程使用前缀/后缀乘积或分布 DP，无除法，可安全处理 0/1 值。
    """
    gtype = gate["type"]
    n = len(child_values)
    out: list[Fraction] = [Fraction(0)] * n

    if gtype == AND:
        # ∂ Π x_j / ∂ x_i = Π_{j≠i} x_j
        pref = [Fraction(1)] * (n + 1)
        suff = [Fraction(1)] * (n + 1)
        for i in range(n):
            pref[i + 1] = pref[i] * child_values[i]
        for i in range(n - 1, -1, -1):
            suff[i] = suff[i + 1] * child_values[i]
        for i in range(n):
            out[i] = adj_in * pref[i] * suff[i + 1]
        return out

    if gtype == OR:
        # out = 1 - Π(1-x_j)；∂out/∂x_i = Π_{j≠i}(1-x_j)
        pref = [Fraction(1)] * (n + 1)
        suff = [Fraction(1)] * (n + 1)
        for i in range(n):
            pref[i + 1] = pref[i] * (1 - child_values[i])
        for i in range(n - 1, -1, -1):
            suff[i] = suff[i + 1] * (1 - child_values[i])
        for i in range(n):
            out[i] = adj_in * pref[i] * suff[i + 1]
        return out

    # KOFN：∂P(至少k个)/∂p_i = P(其余输入中“恰好 k-1 个”发生)。
    # （x_i 由不发生变发生时，输出翻转当且仅当其余恰好 k-1 个发生；
    #   对分布递推式求导并望远镜求和亦得此结果。k=1 退化为 OR 的
    #   “其余全 0”，k=n 退化为 AND 的“其余全 1”。）
    # 对全部子节点（含 0/1 常量）统一做前缀/后缀分布 DP。
    k = gate["k"]
    pref_dist: list[list[Fraction]] = [None] * (n + 1)
    pref_dist[0] = [Fraction(1)]
    for i, v in enumerate(child_values):
        prev = pref_dist[i]
        nxt = [Fraction(0)] * (len(prev) + 1)
        for j, old in enumerate(prev):
            nxt[j] += old * (1 - v)
            nxt[j + 1] += old * v
        pref_dist[i + 1] = nxt
    suff_dist: list[list[Fraction]] = [None] * (n + 1)
    suff_dist[n] = [Fraction(1)]
    for i in range(n - 1, -1, -1):
        prev = suff_dist[i + 1]
        nxt = [Fraction(0)] * (len(prev) + 1)
        v = child_values[i]
        for j, old in enumerate(prev):
            nxt[j] += old * (1 - v)
            nxt[j + 1] += old * v
        suff_dist[i] = nxt
    for i in range(n):
        left = pref_dist[i]
        right = suff_dist[i + 1]
        # 左右发生数之和恰好 k-1：卷积在第 k-1 项。
        coef = Fraction(0)
        for a, pa in enumerate(left):
            b = k - 1 - a
            if 0 <= b < len(right):
                coef += pa * right[b]
        out[i] = adj_in * coef
    return out


# ---- 单个枚举取值下的最小割集树 DP -----------------------------------------


def _merge_sorted(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    """合并两个互不含相同元素的有序元组。"""
    merged: list[str] = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] < b[j]:
            merged.append(a[i])
            i += 1
        else:
            merged.append(b[j])
            j += 1
    merged.extend(a[i:])
    merged.extend(b[j:])
    return tuple(merged)


def _cutset_dp(
    top: str,
    gates: dict[str, dict],
    assignment: dict[str, int],
) -> Optional[tuple[str, ...]]:
    """在共享变量固定为 assignment 的条件下，求最小割集。

    每个节点状态为：
    * None —— 该节点恒不成立（无法通过其余唯一叶子触发）；
    * ()   —— 恒成立（不需要任何叶子发生）；
    * (...)—— 恰好需要这些唯一叶子同时发生（升序）。
    """
    memo: dict[str, Optional[tuple[str, ...]]] = {}

    def visit(node: str) -> Optional[tuple[str, ...]]:
        if node in memo:
            return memo[node]
        gate = gates.get(node)
        if gate is None:
            # 基础故障：在共享变量中则为常量；否则是唯一叶子。
            if node in assignment:
                state = () if assignment[node] == 1 else None
            else:
                state = (node,)
            memo[node] = state
            return state

        gtype = gate["type"]
        child_states = [visit(ref) for ref in gate["inputs"]]
        const_trues = sum(1 for s in child_states if s == ())
        has_false = any(s is None for s in child_states)

        if gtype == AND:
            if has_false:
                state: Optional[tuple[str, ...]] = None
            else:
                merged: tuple[str, ...] = ()
                for s in child_states:
                    merged = _merge_sorted(merged, s)
                state = merged
        elif gtype == OR:
            if const_trues:
                state = ()
            else:
                best: Optional[tuple[str, ...]] = None
                for s in child_states:
                    if s is None:
                        continue
                    if best is None or len(s) < len(best) or (len(s) == len(best) and s < best):
                        best = s
                state = best
        else:  # KOFN
            k = gate["k"]
            n = len(child_states)
            if const_trues >= k:
                state = ()
            elif const_trues + (n - sum(1 for s in child_states if s is None)) < k:
                state = None
            else:
                need = k - const_trues
                vars_states = [s for s in child_states if s not in ((), None)]
                # 组合 DP：comb[j] = 从已处理概率子树中选 j 个发生时，
                # 所需叶子集合的最优解。
                comb: list[Optional[tuple[str, ...]]] = [()] + [None] * len(vars_states)
                occupied = 1
                for s in vars_states:
                    new_comb = comb[:]  # 不选该子树
                    for j in range(occupied):
                        if comb[j] is None:
                            continue
                        cand = _merge_sorted(comb[j], s)
                        jn = j + 1
                        cur = new_comb[jn]
                        if cur is None or len(cand) < len(cur) or (
                            len(cand) == len(cur) and cand < cur
                        ):
                            new_comb[jn] = cand
                    comb = new_comb
                    occupied += 1
                state = comb[need] if need < len(comb) else None
        memo[node] = state
        return state

    return visit(top)


# ---- 主分析流程 ------------------------------------------------------------


def analyze(payload: dict) -> dict:
    model = normalize_payload(payload)
    top = model["top"]
    probs = model["probs"]
    gates = model["gates"]

    mult = compute_multiplicities(top, probs, gates)
    # mult 仅统计从顶事件可达的部分（compute_multiplicities 自顶向下递归，
    # 天然不包含不可达门/基础故障）。
    shared = sorted(b for b, count in mult.items() if count > 1)
    if len(shared) > MAX_REPEATED:
        raise FaultTreeError(
            f"展开顶事件后重复的基础 ID 至多 {MAX_REPEATED} 个，"
            f"实际有 {len(shared)} 个（{', '.join(shared)}），超出可处理规模"
        )

    # 顶事件本身是基础故障的退化情形：它发生即触发，降幅就是 p，
    # 最小割集为该故障本身（即使 p=0，“它发生且其余不发生”也触发）。
    if top in probs:
        p = probs[top]
        reductions = {bid: _frac(p if bid == top else Fraction(0)) for bid in probs}
        return {
            "top_event_probability": _frac(p),
            "probability_reductions": reductions,
            "minimum_cut_set": [top],
        }

    # 门的逆拓扑序（子先于父），用于逐枚举取值的前向/反向计算。
    post_order = _post_order(top, gates)

    s_index = {bid: i for i, bid in enumerate(shared)}
    total_masks = 1 << len(shared)

    top_prob = Fraction(0)
    # 各共享变量“被置为不发生”后的顶事件概率（干预概率）。
    intervened = [Fraction(0)] * len(shared)
    # 各唯一叶子的降幅累加： Σ_mask weight(mask) * d(mask)
    unique_reduction: dict[str, Fraction] = {}

    best_cut: Optional[tuple[str, ...]] = None
    # 缓存每个共享变量取值下的根值（枚举覆盖全部 2^|S| 个取值）。
    root_values: list[Fraction] = [Fraction(0)] * total_masks

    for mask in range(total_masks):
        assignment = {bid: (mask >> s_index[bid]) & 1 for bid in shared}

        # 该取值的概率权重（各共享变量独立）。
        factors = [
            probs[bid] if assignment[bid] == 1 else (1 - probs[bid])
            for bid in shared
        ]
        weight = Fraction(1)
        for factor in factors:
            weight *= factor
        # weight_without[i] = 除第 i 位外其余位的概率因子乘积，
        # 用于“把第 i 位置为不发生”的干预计算（前缀积 × 后缀积，无除法）。
        pref = [Fraction(1)] * (len(shared) + 1)
        suff = [Fraction(1)] * (len(shared) + 1)
        for i, factor in enumerate(factors):
            pref[i + 1] = pref[i] * factor
        for i in range(len(shared) - 1, -1, -1):
            suff[i] = suff[i + 1] * factors[i]
        weight_without = [pref[i] * suff[i + 1] for i in range(len(shared))]

        # ---- 前向：计算每个门/叶子在本取值下的值 ----
        values: dict[str, Fraction] = {}

        def value_of(node: str) -> Fraction:
            if node in values:
                return values[node]
            if node in probs:
                values[node] = Fraction(assignment[node]) if node in assignment else probs[node]
                return values[node]
            gate = gates[node]
            cv = [value_of(ref) for ref in gate["inputs"]]
            values[node] = _forward_gate(gate, cv)
            return values[node]

        root_value = value_of(top)
        root_values[mask] = root_value
        top_prob += weight * root_value

        # ---- 反向：伴随法求唯一叶子的偏导 ----
        adj: dict[str, Fraction] = {gid: Fraction(0) for gid in gates}
        leaf_grad: dict[str, Fraction] = {}
        adj[top] = Fraction(1)
        for node in reversed(post_order):
            gate = gates[node]
            cv = [values[ref] for ref in gate["inputs"]]
            contribs = _backward_gate(gate, cv, adj[node])
            for ref, c in zip(gate["inputs"], contribs):
                if ref in gates:
                    # 门（即使本取值下为 0/1 常量）继续向上游传播：
                    # 它后面可能挂着唯一叶子，且“常量门恰好取该值”
                    # 本身也编码了对其输入发生数的约束。
                    adj[ref] += c
                elif ref not in assignment:
                    # 唯一基础叶子：记录偏导。
                    leaf_grad[ref] = leaf_grad.get(ref, Fraction(0)) + c
                # 共享变量的基础实例不参与反向（用干预公式单独计算）。
        # 反向伴随给出的是 ∂P(top)/∂P(leaf)；“leaf 不发生”的降幅为
        # E[1{leaf=1} · 偏导] = p_leaf · E[偏导]（唯一叶子与门实例一一对应，
        # 其发生指示与偏导中的其余独立因子分离），故累加时乘 p_leaf。
        for bid, grad in leaf_grad.items():
            term = probs[bid] * weight * grad
            unique_reduction[bid] = unique_reduction.get(bid, Fraction(0)) + term

        # ---- 共享变量的干预降幅 ----
        for i in range(len(shared)):
            if (mask >> i) & 1:
                # 干预后该位变 0，权重换成 weight_without[i]，函数值取
                # “该位为 0、其余位不变”取值下的根值（已缓存）。
                mask_zero = mask & ~(1 << i)
                intervened[i] += weight_without[i] * root_values[mask_zero]

        # ---- 最小割集 ----
        # 注意：被枚举置 1 的共享故障是本候选割集的“真实成员”，
        # 必须与树上求得的唯一叶子集合合并后再参与全局比较。
        required_shared = tuple(b for b in shared if assignment[b] == 1)
        tree_cut = _cutset_dp(top, gates, assignment)
        if tree_cut is not None:
            cand = _merge_sorted(required_shared, tree_cut)
            if best_cut is None or len(cand) < len(best_cut) or (
                len(cand) == len(best_cut) and cand < best_cut
            ):
                best_cut = cand

    reductions: dict[str, str] = {}
    for bid in probs:
        if bid in s_index:
            reduction = top_prob - intervened[s_index[bid]]
        else:
            reduction = unique_reduction.get(bid, Fraction(0))
        # 浮点/代数误差不会出现（全程 Fraction）；仅做非负保护。
        if reduction < 0:
            reduction = Fraction(0)
        reductions[bid] = _frac(reduction)

    return {
        "top_event_probability": _frac(top_prob),
        "probability_reductions": reductions,
        "minimum_cut_set": sorted(best_cut) if best_cut is not None else [],
    }


def _post_order(top: str, gates: dict[str, dict]) -> list[str]:
    """从顶事件可达门的逆拓扑序（子在前、父在后）。"""
    seen: set[str] = set()
    order: list[str] = []

    def dfs(node: str) -> None:
        if node in seen or node not in gates:
            return
        seen.add(node)
        for ref in gates[node]["inputs"]:
            dfs(ref)
        order.append(node)

    dfs(top)
    return order


def _frac(value: Fraction) -> str:
    """约分为最简分数字符串。"""
    return f"{value.numerator}/{value.denominator}"
