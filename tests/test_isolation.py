"""证据隔离规划(plan_isolation)的领域测试。

覆盖:
  - 隔离后按原规则(总权重优先, 片段数次之)重算唯一得到所选正文;
  - 目标序: 隔离片段数 -> 隔离权重和 -> 编号升序列表字典序;
  - 不改动片段内容与权重: 重算最优总权重/见证与原重建一致;
  - 必须比较全部可行最优覆盖, 不能只看页面展示的两份正文;
  - 每个被隔离片段单独恢复后重新产生歧义, 并附竞争正文;
  - 对抗规模(2^14 个最优正文)下的性能与正确性。
"""

from __future__ import annotations

import time

import pytest

from backend.solver import Fragment, plan_isolation, solve


def f(fid: str, offset: int, payload: str, weight: int) -> Fragment:
    return Fragment(id=fid, offset=offset, payload=bytes.fromhex(payload), weight=weight)


# ---------- 基础: 隔离一个分歧片段后唯一 ----------

def test_isolate_single_fragment_makes_selected_body_unique():
    frags = [
        f("X", 0, "00", 100),
        f("Y", 0, "01", 100),
        f("Z", 1, "FF", 1),
    ]
    original = solve(2, frags)
    assert original.status == "AMBIGUOUS"

    plan = plan_isolation(2, frags, "00FF")

    assert plan.isolated_fragment_ids == ("Y",)
    # 不改动权重: 剩余证据的最优总权重仍是原最优值, 见证仍是原正文的见证。
    assert plan.recomputed_total_weight == 101
    assert plan.recomputed_fragment_count == 2
    assert plan.witness_fragment_ids == ("X", "Z")
    assert plan.retained_fragment_count == 2
    assert plan.isolated_weight == 100

    item = plan.items[0]
    assert item.id == "Y"
    # 单独恢复 Y 后重新歧义, 竞争正文为 01FF。
    assert item.restored_verdict == "AMBIGUOUS"
    assert item.competitor_hex == "01FF"
    assert set(item.competitor_witness_fragment_ids) == {"Y", "Z"}
    assert item.restored_total_weight == 101


def test_isolate_second_body_is_supported():
    frags = [
        f("X", 0, "00", 100),
        f("Y", 0, "01", 100),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, "01FF")
    assert plan.isolated_fragment_ids == ("X",)
    assert plan.witness_fragment_ids == ("Y", "Z")
    assert plan.items[0].competitor_hex == "00FF"
    assert set(plan.items[0].competitor_witness_fragment_ids) == {"X", "Z"}


def test_selected_hex_is_case_insensitive():
    frags = [f("X", 0, "00", 5), f("Y", 0, "01", 5)]
    plan = plan_isolation(1, frags, "00")
    assert plan.selected_hex == "00"
    plan_lower = plan_isolation(1, frags, "00")
    assert plan_lower.isolated_fragment_ids == ("Y",)


# ---------- 目标序: 片段数优先, 再权重, 再字典序 ----------

def _weight_tie_frags() -> list[Fragment]:
    # 字节0=00 的阵营 {Xa w9, Xb w2}, 字节0=01 的阵营 {Yhi w9, Ylo w2},
    # Z 固定字节1。两个最优正文 00FF / 01FF 的总权重均为 12、片段数均为 3。
    return [
        f("Xa", 0, "00", 9),
        f("Xb", 0, "00", 2),
        f("Yhi", 0, "01", 9),
        f("Ylo", 0, "01", 2),
        f("Z", 1, "FF", 1),
    ]


def test_minimize_count_beats_weight():
    # 每个竞争见证只需要击中一片即可, 即便有更轻的多片方案也必须取单片。
    # 本例目标集合为单元素 {Yhi,Ylo}: 击中任一片即可, 故最优片段数恒为 1;
    # 另构造需要两片的独立冲突位以确认"片数优先"。
    frags = [
        f("A0", 0, "00", 5),
        f("P", 0, "01", 5),
        f("A1", 1, "00", 5),
        f("Q", 1, "01", 5),
    ]
    plan = plan_isolation(2, frags, "0000")
    # 4 个最优正文: 0000/0100/0001/0101; 两个冲突位相互独立, 必须隔离 P 与 Q 两片。
    assert plan.competing_body_count == 4
    assert set(plan.isolated_fragment_ids) == {"P", "Q"}
    assert plan.isolated_weight == 10


def test_minimize_weight_sum_on_count_tie():
    plan = plan_isolation(2, _weight_tie_frags(), "00FF")
    # {Yhi}(权重9) 与 {Ylo}(权重2) 同为单片方案, 取权重更小的 Ylo。
    assert plan.isolated_fragment_ids == ("Ylo",)
    assert plan.isolated_weight == 2


def test_lexicographic_ids_on_full_tie():
    frags = [
        f("Xa", 0, "00", 3),
        f("Xb", 0, "00", 3),
        f("ZB", 0, "01", 3),
        f("AA", 0, "01", 3),
        f("Z", 1, "FF", 1),
    ]
    plan = plan_isolation(2, frags, "00FF")
    # 片数与权重都相同: 取编号升序更小的 AA, 而非 ZB。
    assert plan.isolated_fragment_ids == ("AA",)


def test_multi_fragment_ids_sorted_ascending():
    # 两片都必须隔离时, 列表按编号升序给出。
    frags = [
        f("zeta", 0, "00", 1),
        f("alpha", 0, "01", 1),
        f("beta-tail", 1, "00", 1),
        f("beta-tail2", 1, "01", 1),
    ]
    plan = plan_isolation(2, frags, "0000")
    assert set(plan.isolated_fragment_ids) == {"alpha", "beta-tail2"}
    assert plan.isolated_fragment_ids == tuple(sorted(plan.isolated_fragment_ids))


# ---------- 比较全部可行覆盖, 而非仅展示的两份 ----------

def test_planner_considers_all_optimal_covers_not_just_displayed_two():
    frags = [
        f("A0", 0, "00", 5),
        f("P", 0, "01", 5),
        f("A1", 1, "00", 5),
        f("Q", 1, "01", 5),
    ]
    result = solve(2, frags)
    # 页面只展示字节序最小的两份: 0000 与 0001。
    assert [b.hex for b in result.bodies] == ["0000", "0001"]
    plan = plan_isolation(2, frags, "0000")
    # 若只与展示的两份比较, 会误以为隔离 Q 即可(仅挡住 0001);
    # 但 0100/0101 仍存在, 故正确方案必须同时隔离 P。
    assert set(plan.isolated_fragment_ids) == {"P", "Q"}


# ---------- 逐项反例 ----------

def test_every_isolated_fragment_has_indispensable_counterexample():
    frags = [
        f("A0", 0, "00", 5),
        f("P", 0, "01", 5),
        f("A1", 1, "00", 5),
        f("Q", 1, "01", 5),
    ]
    plan = plan_isolation(2, frags, "0000")
    by_id = {it.id: it for it in plan.items}
    # 恢复 P 时的竞争正文与恢复 Q 时不同, 且都使所选正文失去唯一性。
    assert by_id["P"].restored_verdict == "AMBIGUOUS"
    assert by_id["P"].competitor_hex == "0100"
    assert set(by_id["P"].competitor_witness_fragment_ids) == {"A1", "P"}
    assert by_id["Q"].restored_verdict == "AMBIGUOUS"
    assert by_id["Q"].competitor_hex == "0001"
    assert set(by_id["Q"].competitor_witness_fragment_ids) == {"A0", "Q"}
    # 反例裁决下的最优值也一并给出。
    assert by_id["P"].restored_total_weight == 10
    assert by_id["P"].restored_fragment_count == 2


def test_fragment_contents_and_weights_unchanged():
    frags = [f("X", 0, "ab", 100), f("Y", 0, "cd", 100)]
    original = solve(1, frags)
    plan = plan_isolation(1, frags, "AB")
    # 被隔离片段回显的内容/权重与输入逐字一致(大写十六进制)。
    item = plan.items[0]
    assert item.payload_hex == "CD"
    assert item.weight == 100
    assert item.offset == 0
    # 原重建结果不受规划影响。
    assert original.status == "AMBIGUOUS"
    assert original.total_weight == 100


# ---------- 规模与性能 ----------

def test_28_fragment_isolation_performance():
    # 14 个独立歧义位 -> 2^14 个最优正文, 必须隔离全部 14 个 hi 片段。
    frags: list[Fragment] = []
    for i in range(14):
        frags.append(f(f"lo{i}", i, "00", 1))
        frags.append(f(f"hi{i}", i, "01", 1))
    start = time.monotonic()
    plan = plan_isolation(14, frags, "00" * 14)
    elapsed = time.monotonic() - start
    assert len(plan.isolated_fragment_ids) == 14
    assert plan.competing_body_count == 2 ** 14
    assert plan.recomputed_total_weight == 14
    assert plan.recomputed_fragment_count == 14
    # 每个恢复项都给出一个竞争正文。
    assert all(it.restored_verdict == "AMBIGUOUS" for it in plan.items)
    assert all(it.competitor_hex for it in plan.items)
    assert elapsed < 5.0, f"规划耗时过长: {elapsed:.2f}s"


def test_isolated_ids_unique():
    frags = [
        f("A", 0, "00", 1),
        f("B", 0, "01", 1),
        f("C", 1, "00", 1),
        f("D", 1, "01", 1),
    ]
    plan = plan_isolation(2, frags, "0000")
    ids = plan.isolated_fragment_ids
    assert len(ids) == len(set(ids))


def test_shared_witness_fragment_needs_only_one_isolation():
    # 两个竞争正文 0101 / 0102 都依赖同一片 P(仅覆盖字节0, 取01); 字节1 的 Q1/Q2
    # 与 B 侧的 A1 冲突, 却与 P 相容。故隔离 P 一片即可同时挡住两个竞争正文,
    # 而 {Q1,Q2} 需两片——即使 P 更重, 片数优先仍选 P。
    frags = [
        f("B0", 0, "0000", 9),   # 跨字节0,1, 与 P、Q1、Q2 都冲突
        f("R", 1, "00", 1),      # 与 B0 一致(凑成 2 片见证), 与 Q1/Q2 冲突
        f("P", 0, "01", 8),      # 竞争阵营字节0
        f("Q1", 1, "01", 2),
        f("Q2", 1, "02", 2),
    ]
    result = solve(2, frags)
    assert result.status == "AMBIGUOUS"
    assert result.total_weight == 10
    assert [b.hex for b in result.bodies] == ["0000", "0101"]
    plan = plan_isolation(2, frags, "0000")
    assert plan.competing_body_count == 3  # 0000 / 0101 / 0102
    assert plan.isolated_fragment_ids == ("P",)
    assert plan.isolated_weight == 8
    item = plan.items[0]
    assert item.restored_verdict == "AMBIGUOUS"
    assert item.competitor_hex == "0101"
    assert set(item.competitor_witness_fragment_ids) == {"P", "Q1"}


def test_isolation_plan_serializes():
    frags = [f("X", 0, "00", 5), f("Y", 0, "01", 5)]
    plan = plan_isolation(1, frags, "00")
    data = plan.to_dict()
    assert data["selected_hex"] == "00"
    assert data["isolated_fragment_ids"] == ["Y"]
    assert data["recomputed_optimal"] == {"total_weight": 5, "fragment_count": 1}
    assert data["witness_fragment_ids"] == ["X"]
    item = data["isolated_fragments"][0]
    assert item["id"] == "Y"
    assert item["restored_verdict"] == "AMBIGUOUS"
    assert item["competitor_hex"] == "01"
    assert item["restored_optimal"]["total_weight"] == 5


@pytest.mark.parametrize("selected", ["00FF", "01FF"])
def test_either_selection_yields_witness_consistency(selected: str):
    frags = [f("X", 0, "00", 7), f("Y", 0, "01", 7), f("Z", 1, "FF", 1)]
    plan = plan_isolation(2, frags, selected)
    # 重算后的见证总权重恒等于原最优(8), 与隔离掉的权重无关。
    assert plan.recomputed_total_weight == 8
    kept = set(plan.witness_fragment_ids)
    assert not (set(plan.isolated_fragment_ids) & kept)
