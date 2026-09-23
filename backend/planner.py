"""证据隔离规划(仅在重建裁决为 AMBIGUOUS 时使用)。

复核员从两份候选正文中选定一份目标正文 T。规划器在**不改动任何片段
内容与权重**的前提下, 找出一组暂不采用(隔离)的片段 S, 使其余证据按
既有规则(先最大化总权重, 再最大化片段数)重算后, **唯一**得到 T。

择优次序(依次最小化):
  1. 隔离片段数;
  2. 隔离片段的权重总和;
  3. 隔离编号按字符串升序排列后, 列表字典序最小。

关键观察
========
设 A 为所有"逐字节与 T 一致"的片段, D 为其余片段。
- A 中任意两片在重叠处必然一致(都等于 T), 故全取 A 互不冲突;
- 重算时任何还原 T 的方案只能采用 A 中片段, 而权重均为正, 因此 T 阵营
  的最优方案就是"全取 A", 基准值 (W_A, |A|) 与隔离集无关;
- 原问题中 T 是最优正文之一, 故 A 必然覆盖 [0, L), 且原问题最优总权重
  恰为 W_A; 隔离只会删片段, 任何异正文方案的价值不可能超过基准。

于是规划等价于一个最小击中集问题: 每个"价值追平基准、还原出异正文的
可行覆盖"都至少动用一个 D 中片段; 要找最小代价的 S ⊆ D 击中全部此类
覆盖。判定"是否还存在异正文竞争覆盖"时, 搜索的是**所有**可行覆盖
(带与求解器相同的覆盖/上界剪枝), 而非当前展示的某一个见证。

对每个被隔离片段 i ∈ S, 单独把它放回去重算必然重新出现与 T 竞争的
最优正文(否则 S 可去掉 i, 与其最小性矛盾): 重算裁决直接调用
solver.solve 取得(与线上规则严格一致), 竞争正文及其见证则由上述 DFS
在"强制动员 i"的条件下给出, 使每条反例都直接证明 i 不可省略。
"""

from __future__ import annotations

from dataclasses import dataclass

from .solver import Fragment, BodyWitness, _adjacency, solve


@dataclass(frozen=True)
class IsolatedFragment:
    id: str
    offset: int
    payload_hex: str
    weight: int


@dataclass(frozen=True)
class Counterexample:
    """单独恢复某被隔离片段后的重算裁决(证明该片段不可省略)。"""

    fragment_id: str
    restored_verdict: str  # 重算裁决, 最小方案下必为 AMBIGUOUS
    selected_hex: str
    rival_hex: str
    rival_witness_fragment_ids: tuple[str, ...]
    total_weight: int
    fragment_count: int


@dataclass(frozen=True)
class IsolationPlan:
    selected_hex: str
    isolated_fragments: tuple[IsolatedFragment, ...]
    isolated_weight: int
    total_weight: int
    fragment_count: int
    target_body: BodyWitness
    counterexamples: tuple[Counterexample, ...]

    def to_dict(self) -> dict:
        return {
            "selected_hex": self.selected_hex,
            "isolated_fragment_ids": [f.id for f in self.isolated_fragments],
            "isolated_fragments": [
                {
                    "id": f.id,
                    "offset": f.offset,
                    "payload": f.payload_hex,
                    "weight": f.weight,
                }
                for f in self.isolated_fragments
            ],
            "isolated_weight": self.isolated_weight,
            "optimal": {
                "total_weight": self.total_weight,
                "fragment_count": self.fragment_count,
            },
            "target_body": {
                "rank": self.target_body.rank,
                "hex": self.target_body.hex,
                "witness_fragment_ids": list(
                    self.target_body.witness_fragment_ids
                ),
                "adopted_fragments": [
                    {
                        "id": f.id,
                        "offset": f.offset,
                        "payload": f.payload_hex,
                        "weight": f.weight,
                    }
                    for f in self.target_body.adopted_fragments
                ],
            },
            "counterexamples": [
                {
                    "fragment_id": c.fragment_id,
                    "restored_verdict": c.restored_verdict,
                    "selected_hex": c.selected_hex,
                    "rival_hex": c.rival_hex,
                    "rival_witness_fragment_ids": list(
                        c.rival_witness_fragment_ids
                    ),
                    "optimal": {
                        "total_weight": c.total_weight,
                        "fragment_count": c.fragment_count,
                    },
                }
                for c in self.counterexamples
            ],
        }


def _fragment_matches(frag: Fragment, target: bytes) -> bool:
    return all(
        frag.payload[i] == target[frag.offset + i]
        for i in range(frag.length)
    )


def plan_isolation(
    length: int, fragments: list[Fragment], selected: bytes
) -> IsolationPlan:
    """计算证据隔离方案。

    调用方须保证: 输入已通过全部合法性校验, 原始裁决为 AMBIGUOUS, 且
    selected 是裁决给出的两份候选正文之一。
    """

    n = len(fragments)
    full_mask = (1 << length) - 1

    consistent_set = {
        i for i, frag in enumerate(fragments) if _fragment_matches(frag, selected)
    }
    disagree = [i for i in range(n) if i not in consistent_set]

    base_weight = sum(fragments[i].weight for i in consistent_set)
    base_count = len(consistent_set)

    # 与求解器相同的分支顺序: 偏移升序、权重降序、原始编号兜底。
    order = sorted(
        range(n), key=lambda k: (fragments[k].offset, -fragments[k].weight, k)
    )
    position_of = [0] * n
    for ordered_index, original_index in enumerate(order):
        position_of[original_index] = ordered_index

    adj = _adjacency(fragments)
    adj_o = [0] * n
    for a in range(n):
        for b in range(n):
            if adj[order[a]] & (1 << order[b]):
                adj_o[a] |= 1 << b
    weight_o = [fragments[k].weight for k in order]
    covers_o = [0] * n
    for k in range(n):
        frag = fragments[order[k]]
        m = 0
        for pos in range(frag.offset, frag.end):
            m |= 1 << pos
        covers_o[k] = m
    # 在 order 空间标记"与目标不一致"的片段。
    disagree_o = 0
    for original_index in disagree:
        disagree_o |= 1 << position_of[original_index]

    def find_rival(isolated_o: int, force_o: int = 0) -> int | None:
        """在未隔离片段中搜索一个追平基准、还原异正文的可行覆盖。

        返回所选片段(order 空间)位掩码; 不存在则 None。枚举全部可行覆盖,
        剪枝与 solver.search 一致(覆盖可达性、权重/计数上界)。
        force_o 中的片段被强制选入见证(用于反例: 竞争正文必须动员被
        单独恢复的那一个片段; 隔离集的最小性保证这样的覆盖必然存在)。
        """

        init_total = 0
        init_count = 0
        init_covered = 0
        bits = force_o
        while bits:
            k = (bits & -bits).bit_length() - 1
            init_total += weight_o[k]
            init_count += 1
            init_covered |= covers_o[k]
            bits &= bits - 1

        def dfs(
            i: int,
            chosen: int,
            total: int,
            count: int,
            covered: int,
            differs: bool,
        ) -> int | None:
            if i < n and (force_o >> i) & 1:
                # 强制片段已在 chosen 中, 跳过该决策位。
                return dfs(i + 1, chosen, total, count, covered, differs)

            upper_weight = total
            upper_count = count
            reachable = covered
            for k in range(i, n):
                if (isolated_o >> k) & 1 or (force_o >> k) & 1:
                    continue
                if not (adj_o[k] & chosen):
                    upper_weight += weight_o[k]
                    upper_count += 1
                    reachable |= covers_o[k]
            if reachable != full_mask:
                return None
            if (upper_weight, upper_count) < (base_weight, base_count):
                return None

            if i == n:
                if (
                    covered == full_mask
                    and differs
                    and (total, count) >= (base_weight, base_count)
                ):
                    return chosen
                return None

            # 包含优先(与求解器一致), 与已选相容且未被隔离才可采用。
            if not ((isolated_o >> i) & 1 or (adj_o[i] & chosen)):
                hit = dfs(
                    i + 1,
                    chosen | (1 << i),
                    total + weight_o[i],
                    count + 1,
                    covered | covers_o[i],
                    differs or bool((disagree_o >> i) & 1),
                )
                if hit is not None:
                    return hit
            return dfs(i + 1, chosen, total, count, covered, differs)

        return dfs(
            0, force_o, init_total, init_count, init_covered,
            bool(force_o & disagree_o),
        )

    # ---- 最小击中集: 分支定界 ----
    # 状态以 order 空间的隔离位掩码表示; 不同分支顺序可能到达同一状态,
    # 缓存其竞争覆盖判定。
    rival_cache: dict[int, int | None] = {}

    def rival_of(isolated_o: int) -> int | None:
        if isolated_o not in rival_cache:
            rival_cache[isolated_o] = find_rival(isolated_o)
        return rival_cache[isolated_o]

    best: tuple[int, int, tuple[str, ...]] | None = None
    best_mask_o = 0

    def search(isolated_o: int, iso_count: int, iso_weight: int) -> None:
        nonlocal best, best_mask_o

        rival_o = rival_of(isolated_o)
        if rival_o is None:
            # 当前隔离集已击中全部异正文竞争覆盖, 本身即是候选解;
            # 再隔离只会增大(片段数, 权重和), 无需下探。
            ids = tuple(
                sorted(
                    fragments[order[k]].id
                    for k in range(n)
                    if (isolated_o >> k) & 1
                )
            )
            cand = (iso_count, iso_weight, ids)
            if best is None or cand < best:
                best = cand
                best_mask_o = isolated_o
            return

        # 还存在竞争覆盖: 必须再隔离至少一个片段(权重为正整数, 至少 +1)。
        if best is not None:
            next_count = iso_count + 1
            if next_count > best[0]:
                return
            if next_count == best[0] and iso_weight + 1 > best[1]:
                return

        # 该竞争覆盖动用的 D 中片段: 至少隔离其中一个才能将其击破。
        # 先试权重小、编号小者, 以尽早得到优质可行解、强化剪枝。
        choices = [
            k
            for k in range(n)
            if (rival_o >> k) & 1 and (disagree_o >> k) & 1
        ]
        choices.sort(key=lambda k: (weight_o[k], fragments[order[k]].id))
        for k in choices:
            bit = 1 << k
            if isolated_o & bit:
                continue
            search(
                isolated_o | bit,
                iso_count + 1,
                iso_weight + weight_o[k],
            )

    search(0, 0, 0)
    assert best is not None, "AMBIGUOUS 输入必然存在隔离方案(隔离全部不一致片段)"

    isolated_original = sorted(
        order[k] for k in range(n) if (best_mask_o >> k) & 1
    )
    iso_set = set(isolated_original)

    # ---- 重算(隔离全部 S): 权威结果直接取自 solver ----
    kept_all = [frag for i, frag in enumerate(fragments) if i not in iso_set]
    final = solve(length, kept_all)
    assert final.status == "UNIQUE"
    assert final.bodies[0].hex == selected.hex().upper()

    isolated_frags = tuple(
        IsolatedFragment(
            id=fragments[i].id,
            offset=fragments[i].offset,
            payload_hex=fragments[i].payload.hex().upper(),
            weight=fragments[i].weight,
        )
        for i in sorted(isolated_original, key=lambda i: fragments[i].id)
    )

    # ---- 逐项反例: 单独恢复该片段后重算 ----
    # 最小性保证恢复 i 后重新出现异正文; 但"字节序最小的异正文"未必动员 i,
    # 因此用 force_o 强制竞争覆盖包含 i, 使每条反例都直接证明 i 不可省略。
    selected_hex = selected.hex().upper()
    counterexamples: list[Counterexample] = []
    for i in sorted(isolated_original, key=lambda i: fragments[i].id):
        k = position_of[i]
        restored_isolated_o = best_mask_o ^ (1 << k)
        restored_kept = [
            frag for j, frag in enumerate(fragments) if j not in iso_set or j == i
        ]
        # 权威裁决仍由求解器给出(与线上重算规则严格一致)。
        r = solve(length, restored_kept)
        assert r.status == "AMBIGUOUS", (
            f"隔离方案疑似非最小: 恢复 {fragments[i].id!r} 后裁决为 {r.status}"
        )

        rival_o = find_rival(restored_isolated_o, force_o=1 << k)
        assert rival_o is not None and (rival_o & (1 << k)), (
            f"内部错误: 恢复 {fragments[i].id!r} 后找不到动员它的竞争覆盖"
        )
        rival_buf = bytearray(length)
        rival_ids: list[str] = []
        rival_weight = 0
        for original_index, frag in enumerate(fragments):
            if rival_o & (1 << position_of[original_index]):
                for p, byte in enumerate(frag.payload):
                    rival_buf[frag.offset + p] = byte
                rival_ids.append(frag.id)
                rival_weight += frag.weight
        assert (rival_weight, len(rival_ids)) == (
            r.total_weight,
            r.fragment_count,
        )
        counterexamples.append(
            Counterexample(
                fragment_id=fragments[i].id,
                restored_verdict=r.status,
                selected_hex=selected_hex,
                rival_hex=bytes(rival_buf).hex().upper(),
                rival_witness_fragment_ids=tuple(rival_ids),
                total_weight=r.total_weight,  # type: ignore[arg-type]
                fragment_count=r.fragment_count,  # type: ignore[arg-type]
            )
        )

    return IsolationPlan(
        selected_hex=selected_hex,
        isolated_fragments=isolated_frags,
        isolated_weight=sum(fragments[i].weight for i in isolated_original),
        total_weight=final.total_weight,  # type: ignore[arg-type]
        fragment_count=final.fragment_count,  # type: ignore[arg-type]
        target_body=final.bodies[0],
        counterexamples=tuple(counterexamples),
    )
