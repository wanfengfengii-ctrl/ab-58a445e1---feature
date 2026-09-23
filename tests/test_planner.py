"""证据隔离规划的领域测试。

覆盖:
- 基本隔离与重算最优值/见证;
- 三级择优(隔离数 -> 隔离权重和 -> 编号字典序);
- 逐项反例(恢复后必重新歧义, 竞争正文与见证, 见证必含恢复片段);
- rank-1 / rank-2 两份候选正文均可作为目标;
- 与全子集暴力枚举的随机交叉验证;
- 28 片段规模下的性能。
"""

from __future__ import annotations

import random
import time
from itertools import combinations

import pytest

from backend.planner import plan_isolation
from backend.solver import Fragment, solve


def f(fid: str, offset: int, payload: str, weight: int) -> Fragment:
    return Fragment(
        id=fid, offset=offset, payload=bytes.fromhex(payload), weight=weight
    )


# ---------- 基本功能 ----------

def test_isolate_rival_makes_target_unique():
    # 字节 0: 00(A) 与 01(B) 等权; 字节 1 由 Z 固定。
    frags = [
        f("A", 0, "00", 100),
        f("B", 0, "01", 100),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, bytes.fromhex("00FF"))

    assert [x.id for x in plan.isolated_fragments] == ["B"]
    assert plan.isolated_weight == 100
    # 重算后最优值与见证。
    assert (plan.total_weight, plan.fragment_count) == (101, 2)
    assert plan.target_body.hex == "00FF"
    assert set(plan.target_body.witness_fragment_ids) == {"A", "Z"}
    assert plan.selected_hex == "00FF"


def test_isolation_recomputation_is_genuinely_unique():
    frags = [
        f("A", 0, "00", 100),
        f("B", 0, "01", 100),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, bytes.fromhex("00FF"))
    kept = [
        fr
        for fr in frags
        if fr.id not in {x.id for x in plan.isolated_fragments}
    ]
    r = solve(2, kept)
    assert r.status == "UNIQUE"
    assert r.bodies[0].hex == "00FF"


def test_counterexample_shows_competing_body_after_restore():
    frags = [
        f("A", 0, "00", 100),
        f("B", 0, "01", 100),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, bytes.fromhex("00FF"))

    assert len(plan.counterexamples) == 1
    ce = plan.counterexamples[0]
    assert ce.fragment_id == "B"
    assert ce.restored_verdict == "AMBIGUOUS"
    assert ce.selected_hex == "00FF"
    assert ce.rival_hex == "01FF"
    # 竞争正文的见证必须包含被恢复的片段(否则无需隔离它)。
    assert "B" in ce.rival_witness_fragment_ids
    assert "Z" in ce.rival_witness_fragment_ids
    assert (ce.total_weight, ce.fragment_count) == (101, 2)


def test_rank2_body_can_be_selected():
    frags = [
        f("A", 0, "00", 100),
        f("B", 0, "01", 100),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, bytes.fromhex("01FF"))
    assert [x.id for x in plan.isolated_fragments] == ["A"]
    assert plan.target_body.hex == "01FF"
    assert set(plan.target_body.witness_fragment_ids) == {"B", "Z"}
    ce = plan.counterexamples[0]
    assert ce.rival_hex == "00FF"
    assert "A" in ce.rival_witness_fragment_ids


def test_all_consistent_fragments_adopted_in_target_witness():
    # 与目标一致的片段互不冲突, 权重为正 -> 全部被采用。
    frags = [
        f("A1", 0, "00", 4),
        f("A2", 0, "00", 1),
        f("B1", 0, "01", 3),
        f("B2", 0, "01", 2),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, bytes.fromhex("00FF"))
    assert set(plan.target_body.witness_fragment_ids) == {"A1", "A2", "Z"}


# ---------- 三级择优 ----------

def test_min_isolated_weight_breaks_count_tie():
    # 双方各自两片等权叠加: 00 方 4+1=5, 01 方 3+2=5, 各 2 片; Z 共用。
    # 隔离 1 片即可定谳: 在 01 方中隔离权重最小的 B2(2) 而非 B1(3)。
    frags = [
        f("A1", 0, "00", 4),
        f("A2", 0, "00", 1),
        f("B1", 0, "01", 3),
        f("B2", 0, "01", 2),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, bytes.fromhex("00FF"))
    assert [x.id for x in plan.isolated_fragments] == ["B2"]
    assert plan.isolated_weight == 2
    kept = [fr for fr in frags if fr.id != "B2"]
    assert solve(2, kept).status == "UNIQUE"
    # 隔离更重的 B1 也可行, 但权重和更大, 不应被选中。
    kept_alt = [fr for fr in frags if fr.id != "B1"]
    assert solve(2, kept_alt).status == "UNIQUE"


def test_fragment_count_outranks_weight():
    # 目标阵营: T0(00CC, 两字节, w=98) + 与之一致的 C(字节0=00, w=3)
    #   -> {T0,C} 还原 00CC, 值 (101, 2)。
    # 异路: D(01, w=100) 搭配 E1(AA) 或 E2(BB) -> 01AA / 01BB, 均 (101, 2)。
    # C 与 D 在字节 0 冲突, T0 与 E1/E2 在字节 1 冲突, 混合搭配价值远低。
    # 击破全部异正文: 隔离 D 一片(w=100), 或隔离 E1、E2 两片(合计 w=2)。
    # 隔离数优先 -> 必须只隔离 D, 尽管其权重远高于两片之和。
    frags = [
        f("T0", 0, "00CC", 98),
        f("C", 0, "00", 3),
        f("D", 0, "01", 100),
        f("E1", 1, "AA", 1),
        f("E2", 1, "BB", 1),
    ]
    r = solve(2, frags)
    assert r.status == "AMBIGUOUS"
    assert [b.hex for b in r.bodies] == ["00CC", "01AA"]
    plan = plan_isolation(2, frags, bytes.fromhex("00CC"))
    assert [x.id for x in plan.isolated_fragments] == ["D"]
    assert plan.isolated_weight == 100
    # 隔离两片轻权 E1/E2 虽也能定谳且权重和更小, 但片段数更多, 不得入选。
    assert solve(2, [fr for fr in frags if fr.id not in {"E1", "E2"}]).status == "UNIQUE"
    # 反例: 恢复 D 后异正文回来, 且竞争见证必含 D。
    ce = plan.counterexamples[0]
    assert ce.fragment_id == "D"
    assert ce.rival_hex in {"01AA", "01BB"}
    assert "D" in ce.rival_witness_fragment_ids


def test_lexicographic_id_break_when_count_and_weight_tie():
    # 两个互斥单字节候选权重相同: 隔离 ZETA 或 ALFA 均可, 编号升序取 ALFA。
    frags = [
        f("ZETA", 0, "00", 7),
        f("ALFA", 0, "01", 7),
    ]
    plan = plan_isolation(1, frags, bytes.fromhex("00"))
    assert [x.id for x in plan.isolated_fragments] == ["ALFA"]


def test_weight_tie_between_multi_cover_witnesses_uses_id_order():
    # 目标 0000: A(两字节,w7)+E(pos0=00,w1) 见证, 值 (8,2)。
    # 异正文 0101: R2(pos0=01,w4)+R1(pos1=01,w4) 见证, 值 (8,2);
    # 两个位置各自只有 R2 / R1 能盖取值 01, 故隔离任意一片都会令异正文消失。
    # 隔离 R1 或 R2: 隔离数(1)与权重和(4)完全相同 -> 编号字典序取 R1。
    frags = [
        f("A", 0, "0000", 7),
        f("E", 0, "00", 1),
        f("R2", 0, "01", 4),
        f("R1", 1, "01", 4),
    ]
    r = solve(2, frags)
    assert r.status == "AMBIGUOUS"
    assert [b.hex for b in r.bodies] == ["0000", "0101"]
    plan = plan_isolation(2, frags, bytes.fromhex("0000"))
    assert [x.id for x in plan.isolated_fragments] == ["R1"]
    assert plan.isolated_weight == 4
    # 两个一片方案都确实可行, 仅字典序不同。
    assert solve(2, [x for x in frags if x.id != "R1"]).status == "UNIQUE"
    assert solve(2, [x for x in frags if x.id != "R2"]).status == "UNIQUE"
    ce = plan.counterexamples[0]
    assert ce.fragment_id == "R1"
    assert ce.rival_hex == "0101"
    assert "R1" in ce.rival_witness_fragment_ids


def test_isolated_fragments_sorted_by_id_and_weights_summed():
    frags = [
        f("mid", 0, "01", 5),
        f("beg", 0, "02", 5),
        f("end", 0, "03", 5),
        f("T", 0, "00", 5),
    ]
    r = solve(1, frags)
    assert r.status == "AMBIGUOUS"
    plan = plan_isolation(1, frags, bytes.fromhex("00"))
    ids = [x.id for x in plan.isolated_fragments]
    assert ids == sorted(ids)
    assert plan.isolated_weight == 15
    # 每个被隔离片段都有独立反例。
    assert {ce.fragment_id for ce in plan.counterexamples} == set(ids)


# ---------- 反例完备性 ----------

def test_each_counterexample_independently_reproduces_ambiguity():
    # 14 个位置两路等权 -> 需隔离一路(14 片), 逐项恢复都应重新歧义。
    frags = []
    for i in range(7):
        frags.append(f(f"lo{i}", i, "00", 1))
        frags.append(f(f"hi{i}", i, "01", 1))
    plan = plan_isolation(7, frags, b"\x00" * 7)
    iso_ids = {x.id for x in plan.isolated_fragments}
    assert iso_ids == {f"hi{i}" for i in range(7)}

    for ce in plan.counterexamples:
        kept = [fr for fr in frags if fr.id not in iso_ids or fr.id == ce.fragment_id]
        r = solve(7, kept)
        assert r.status == "AMBIGUOUS"
        rival_hexes = [b.hex for b in r.bodies if b.hex != ce.selected_hex]
        assert ce.rival_hex in rival_hexes
        # 竞争正文见证必含恢复的片段。
        rival = next(b for b in r.bodies if b.hex == ce.rival_hex)
        assert ce.fragment_id in rival.witness_fragment_ids
        assert (ce.total_weight, ce.fragment_count) == (
            r.total_weight,
            r.fragment_count,
        )


def test_removing_any_single_isolation_still_ambiguous():
    # 最小性: 少隔离任意一片都不能唯一(隔离方案不可省略任意成员)。
    frags = [
        f("x0", 0, "00", 3), f("x1", 0, "01", 3),
        f("y0", 1, "00", 3), f("y1", 1, "01", 3),
        f("z0", 2, "00", 3), f("z1", 2, "01", 3),
    ]
    plan = plan_isolation(3, frags, b"\x00\x00\x00")
    iso_ids = [x.id for x in plan.isolated_fragments]
    assert len(iso_ids) == 3
    for drop in iso_ids:
        kept = [
            fr
            for fr in frags
            if fr.id not in iso_ids or fr.id == drop
        ]
        assert solve(3, kept).status == "AMBIGUOUS"


def test_no_fragment_content_or_weight_modified():
    frags = [
        f("A", 0, "deAd", 100),
        f("B", 0, "Beef", 100),
    ]
    before = [(x.id, x.offset, x.payload, x.weight) for x in frags]
    plan_isolation(2, frags, bytes.fromhex("DEAD"))
    after = [(x.id, x.offset, x.payload, x.weight) for x in frags]
    assert before == after


# ---------- 规模与性能 ----------

def test_28_fragments_isolation_performance():
    frags = []
    for i in range(14):
        frags.append(f(f"lo{i}", i, "00", 1))
        frags.append(f(f"hi{i}", i, "01", 1))
    t0 = time.time()
    plan = plan_isolation(14, frags, b"\x00" * 14)
    elapsed = time.time() - t0
    assert elapsed < 2.0
    assert len(plan.isolated_fragments) == 14
    assert len(plan.counterexamples) == 14
    assert plan.total_weight == 14
    assert plan.fragment_count == 14


# ---------- 随机暴力交叉验证 ----------

def _make_random_frags(rng: random.Random, n: int, length: int) -> list[Fragment]:
    frags: list[Fragment] = []
    for i in range(n):
        off = rng.randrange(0, length)
        ln = rng.randrange(1, min(length - off, 4) + 1)
        payload = bytes(rng.randrange(256) for _ in range(ln))
        frags.append(Fragment(f"F{i:02d}", off, payload, rng.randrange(1, 8)))
    return frags


def _brute_best(
    length: int, frags: list[Fragment], target_hex: str
) -> tuple[int, int, tuple[str, ...]] | None:
    n = len(frags)
    best: tuple[int, int, tuple[str, ...]] | None = None
    for k in range(n + 1):
        for combo in combinations(range(n), k):
            iso = set(combo)
            kept = [fr for j, fr in enumerate(frags) if j not in iso]
            r = solve(length, kept)
            if r.status == "UNIQUE" and r.bodies[0].hex == target_hex:
                cand = (
                    k,
                    sum(frags[j].weight for j in combo),
                    tuple(sorted(frags[j].id for j in combo)),
                )
                if best is None or cand < best:
                    best = cand
    return best


@pytest.mark.parametrize("rank", [1, 2])
def test_random_plans_match_brute_force(rank: int):
    rng = random.Random(2026 + rank)
    checked = 0
    for _ in range(2500):
        length = rng.randrange(1, 6)
        n = rng.randrange(2, 9)
        frags = _make_random_frags(rng, n, length)
        r = solve(length, frags)
        if r.status != "AMBIGUOUS":
            continue
        target_hex = r.bodies[rank - 1].hex
        plan = plan_isolation(length, frags, bytes.fromhex(target_hex))
        got = (
            len(plan.isolated_fragments),
            plan.isolated_weight,
            tuple(x.id for x in plan.isolated_fragments),
        )
        assert got == _brute_best(length, frags, target_hex)
        # 重算唯一, 反例齐备。
        iso_ids = {x.id for x in plan.isolated_fragments}
        kept = [fr for fr in frags if fr.id not in iso_ids]
        rr = solve(length, kept)
        assert rr.status == "UNIQUE" and rr.bodies[0].hex == target_hex
        for ce in plan.counterexamples:
            restored = kept + [next(fr for fr in frags if fr.id == ce.fragment_id)]
            cr = solve(length, restored)
            assert cr.status == "AMBIGUOUS"
            assert ce.rival_hex in {b.hex for b in cr.bodies}
        checked += 1
        if checked >= 120:
            break
    assert checked >= 50
