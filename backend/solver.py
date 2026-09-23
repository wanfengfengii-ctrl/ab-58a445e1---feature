"""核心重建引擎。

输入: 目标正文长度 L(1..512) 与若干(n<=28)字节片段。
每个片段有唯一编号、零基偏移、非空十六进制载荷与可信权重(1..1_000_000)。

有效方案: 选出的片段两两在重叠处字节完全一致, 且其并集覆盖 [0, L) 的每个字节。
择优: 先最大化总权重; 总权重相同再最大化采用片段数。
裁决:
  - UNIQUE: 所有最优方案还原同一正文;
  - AMBIGUOUS: 最优方案可还原多种正文, 按无符号字节序列序给出最小的两份,
    并附各自的片段见证;
  - IMPOSSIBLE: 不存在完整一致的覆盖(存在缺口, 或片段冲突使覆盖无法完成)。

证据隔离(plan_isolation): 在不改动任何片段内容与权重的前提下, 选出一组
"暂不采用"的片段 Q, 使剩余证据按同一套(总权重优先, 片段数次之)规则重算后,
唯一得到复核员指定的正文。Q 依次最小化: 片段数 -> 权重总和 -> 编号升序列表的
字典序。对每个 q ∈ Q, 单独恢复 q 都必须使该正文重新失去唯一最优地位(裁决
不再为 UNIQUE), 并给出此时的竞争正文作为反例, 以证明 q 在方案中不可省略。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Fragment:
    """一条经过校验的字节片段。payload 为原始字节。"""

    id: str
    offset: int
    payload: bytes
    weight: int

    @property
    def length(self) -> int:
        return len(self.payload)

    @property
    def end(self) -> int:
        return self.offset + len(self.payload)


@dataclass(frozen=True)
class ConflictAlternative:
    position: int
    byte: int
    fragment_ids: tuple[str, ...]


@dataclass(frozen=True)
class WitnessFragment:
    id: str
    offset: int
    payload_hex: str
    weight: int


@dataclass(frozen=True)
class BodyWitness:
    rank: int
    hex: str
    witness_fragment_ids: tuple[str, ...]
    adopted_fragments: tuple[WitnessFragment, ...]


@dataclass(frozen=True)
class ReconstructionResult:
    status: str  # UNIQUE / AMBIGUOUS / IMPOSSIBLE
    target_length: int
    total_weight: Optional[int]
    fragment_count: Optional[int]
    bodies: tuple[BodyWitness, ...]
    conflicts: tuple[ConflictAlternative, ...]
    conflict_positions: tuple[int, ...]
    uncovered_positions: tuple[int, ...]
    impossible_reason: Optional[str]  # GAP / CONFLICT / None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "target_length": self.target_length,
            "optimal": {
                "total_weight": self.total_weight,
                "fragment_count": self.fragment_count,
            },
            "bodies": [
                {
                    "rank": b.rank,
                    "hex": b.hex,
                    "witness_fragment_ids": list(b.witness_fragment_ids),
                    "adopted_fragments": [
                        {
                            "id": f.id,
                            "offset": f.offset,
                            "payload": f.payload_hex,
                            "weight": f.weight,
                        }
                        for f in b.adopted_fragments
                    ],
                }
                for b in self.bodies
            ],
            "conflicts": [
                {
                    "position": c.position,
                    "byte": f"{c.byte:02X}",
                    "fragment_ids": list(c.fragment_ids),
                }
                for c in self.conflicts
            ],
            "conflict_positions": list(self.conflict_positions),
            "uncovered_positions": list(self.uncovered_positions),
            "impossible_reason": self.impossible_reason,
        }


@dataclass(frozen=True)
class IsolatedFragment:
    """证据隔离方案中的一个被隔离片段及其必要性反例。"""

    id: str
    offset: int
    payload_hex: str
    weight: int
    # 单独恢复该片段(其余隔离项仍生效)后重算的裁决: UNIQUE / AMBIGUOUS / IMPOSSIBLE。
    restored_verdict: str
    # 恢复后与所选正文竞争的最优正文(无符号字节序最小的一份, 且异于所选正文)。
    competitor_hex: Optional[str]
    competitor_witness_fragment_ids: tuple[str, ...]
    # 恢复后的最优值。
    restored_total_weight: Optional[int]
    restored_fragment_count: Optional[int]


@dataclass(frozen=True)
class IsolationPlan:
    """证据隔离方案: 隔离 Q 后, 剩余证据唯一还原 selected_hex。"""

    selected_hex: str
    isolated_fragment_ids: tuple[str, ...]
    isolated_weight: int
    retained_fragment_count: int
    # 隔离后重算的最优值与唯一见证。
    recomputed_total_weight: int
    recomputed_fragment_count: int
    witness_fragment_ids: tuple[str, ...]
    items: tuple[IsolatedFragment, ...]
    # 参与比较的全部可行最优覆盖数(正文数), 证明方案比较了所有覆盖而非单一见证。
    competing_body_count: int

    def to_dict(self) -> dict:
        return {
            "selected_hex": self.selected_hex,
            "isolated_fragment_ids": list(self.isolated_fragment_ids),
            "isolated_weight": self.isolated_weight,
            "retained_fragment_count": self.retained_fragment_count,
            "recomputed_optimal": {
                "total_weight": self.recomputed_total_weight,
                "fragment_count": self.recomputed_fragment_count,
            },
            "witness_fragment_ids": list(self.witness_fragment_ids),
            "competing_body_count": self.competing_body_count,
            "isolated_fragments": [
                {
                    "id": it.id,
                    "offset": it.offset,
                    "payload": it.payload_hex,
                    "weight": it.weight,
                    "restored_verdict": it.restored_verdict,
                    "competitor_hex": it.competitor_hex,
                    "competitor_witness_fragment_ids": list(
                        it.competitor_witness_fragment_ids
                    ),
                    "restored_optimal": {
                        "total_weight": it.restored_total_weight,
                        "fragment_count": it.restored_fragment_count,
                    },
                }
                for it in self.items
            ],
        }


def _build_conflicts(
    length: int, fragments: list[Fragment]
) -> tuple[list[ConflictAlternative], list[int], list[int]]:
    """扫描每个目标字节, 返回冲突候选值、冲突位置与缺口位置。"""

    # position -> byte value -> 覆盖该字节且取该值的片段编号
    by_position: list[dict[int, list[str]]] = [{} for _ in range(length)]
    for frag in fragments:
        for i, byte in enumerate(frag.payload):
            pos = frag.offset + i
            by_position[pos].setdefault(byte, []).append(frag.id)

    conflicts: list[ConflictAlternative] = []
    conflict_positions: list[int] = []
    uncovered: list[int] = []
    for pos, values in enumerate(by_position):
        if not values:
            uncovered.append(pos)
            continue
        if len(values) >= 2:
            conflict_positions.append(pos)
            for byte, ids in sorted(values.items()):
                conflicts.append(
                    ConflictAlternative(pos, byte, tuple(ids))
                )
    return conflicts, conflict_positions, uncovered


def _adjacency(fragments: list[Fragment]) -> list[int]:
    """冲突图: 两片段区间相交且存在不同字节则相邻(不可同时采用)。"""

    n = len(fragments)
    adj = [0] * n
    for i in range(n):
        fi = fragments[i]
        for j in range(i + 1, n):
            fj = fragments[j]
            lo = max(fi.offset, fj.offset)
            hi = min(fi.end, fj.end)
            if lo >= hi:
                continue
            disagree = False
            for pos in range(lo, hi):
                if fi.payload[pos - fi.offset] != fj.payload[pos - fj.offset]:
                    disagree = True
                    break
            if disagree:
                adj[i] |= 1 << j
                adj[j] |= 1 << i
    return adj


def _enumerate_optimal(
    length: int, fragments: list[Fragment]
) -> Optional[tuple[int, int, dict[bytes, int]]]:
    """枚举全部达到最优(总权重, 片段数)的方案。

    返回 (best_weight, best_count, {正文 bytes: 所选片段的原始下标位掩码})。
    正文 -> 位掩码 仅保留任一见证; 事实上权重均为正时, 给定正文的最优见证
    必然唯一(凡与正文逐字节一致的片段必被全取, 分歧者必被排除)。
    调用方需先完成缺口(GAP)判定; 返回 None 仅源于冲突使覆盖无法完成。
    """

    n = len(fragments)
    full_mask = (1 << length) - 1

    adj = _adjacency(fragments)
    cover_masks = []
    for frag in fragments:
        m = 0
        for pos in range(frag.offset, frag.end):
            m |= 1 << pos
        cover_masks.append(m)

    # 按偏移自左向右, 权重高者优先, 使包含分支尽早给出高质量可行覆盖。
    order = sorted(range(n), key=lambda k: (fragments[k].offset, -fragments[k].weight, k))
    # order 索引 -> 原始索引的逆映射。
    position_of = [0] * n
    for ordered_index, original_index in enumerate(order):
        position_of[original_index] = ordered_index
    weights = [fragments[k].weight for k in order]
    covers = [cover_masks[k] for k in order]
    # 把邻接位重映到 order 索引空间。
    adj_o = [0] * n
    for a in range(n):
        for b in range(n):
            if adj[order[a]] & (1 << order[b]):
                adj_o[a] |= 1 << b

    best_weight = -1
    best_count = -1
    # body bytes -> 所选片段在 *原始* 下标空间的位掩码。
    bodies: dict[bytes, int] = {}

    def to_original_mask(chosen_o: int) -> int:
        orig = 0
        bits = chosen_o
        while bits:
            k = (bits & -bits).bit_length() - 1
            orig |= 1 << order[k]
            bits &= bits - 1
        return orig

    def render(chosen_o: int) -> bytes:
        buf = bytearray(length)
        bits = chosen_o
        while bits:
            k = (bits & -bits).bit_length() - 1
            frag = fragments[order[k]]
            for i, byte in enumerate(frag.payload):
                buf[frag.offset + i] = byte
            bits &= bits - 1
        return bytes(buf)

    def record(chosen_o: int, total: int, count: int) -> None:
        nonlocal best_weight, best_count, bodies
        if (total, count) < (best_weight, best_count):
            return
        body = render(chosen_o)
        if (total, count) > (best_weight, best_count):
            best_weight, best_count = total, count
            bodies = {body: to_original_mask(chosen_o)}
        else:
            bodies.setdefault(body, to_original_mask(chosen_o))

    def search(i: int, chosen: int, total: int, count: int, covered: int) -> None:
        # 上界: 当前值 + 所有仍与已选相容的剩余片段(全取)。权重均为正,
        # 若该上界在(总权重, 片段数)序上仍不优于已知最优即可剪枝。
        upper_weight = total
        upper_count = count
        reachable = covered
        for k in range(i, n):
            if not (adj_o[k] & chosen):
                upper_weight += weights[k]
                upper_count += 1
                reachable |= covers[k]
        if reachable != full_mask:
            return  # 剩余相容片段即便全取也补不齐覆盖。
        if (upper_weight, upper_count) < (best_weight, best_count):
            return

        if i == n:
            if covered == full_mask:
                record(chosen, total, count)
            return

        # 包含优先: 与已选无冲突才可取。
        if not (adj_o[i] & chosen):
            search(
                i + 1,
                chosen | (1 << i),
                total + weights[i],
                count + 1,
                covered | covers[i],
            )
        # 排除分支。
        search(i + 1, chosen, total, count, covered)

    search(0, 0, 0, 0, 0)

    if best_weight < 0:
        return None
    return best_weight, best_count, bodies


def _make_witness(
    rank: int,
    body: bytes,
    chosen_original: int,
    fragments: list[Fragment],
) -> BodyWitness:
    ids: list[str] = []
    adopted: list[WitnessFragment] = []
    for original_index, frag in enumerate(fragments):
        if chosen_original & (1 << original_index):
            ids.append(frag.id)
            adopted.append(
                WitnessFragment(frag.id, frag.offset, frag.payload.hex().upper(), frag.weight)
            )
    return BodyWitness(
        rank=rank,
        hex=body.hex().upper(),
        witness_fragment_ids=tuple(ids),
        adopted_fragments=tuple(adopted),
    )


def solve(length: int, fragments: list[Fragment]) -> ReconstructionResult:
    """求解重建问题。调用方需已完成全部输入合法性校验。"""

    conflicts, conflict_positions, uncovered = _build_conflicts(length, fragments)

    # 若存在任何片段都覆盖不到的位置, 任何选法都不可能完整。
    if uncovered:
        return ReconstructionResult(
            status="IMPOSSIBLE",
            target_length=length,
            total_weight=None,
            fragment_count=None,
            bodies=(),
            conflicts=tuple(conflicts),
            conflict_positions=tuple(conflict_positions),
            uncovered_positions=tuple(uncovered),
            impossible_reason="GAP",
        )

    enumeration = _enumerate_optimal(length, fragments)

    if enumeration is None:
        return ReconstructionResult(
            status="IMPOSSIBLE",
            target_length=length,
            total_weight=None,
            fragment_count=None,
            bodies=(),
            conflicts=tuple(conflicts),
            conflict_positions=tuple(conflict_positions),
            uncovered_positions=(),
            impossible_reason="CONFLICT",
        )

    best_weight, best_count, bodies = enumeration

    # bytes 比较即无符号字节的字典序。
    sorted_bodies = sorted(bodies.items())
    if len(sorted_bodies) == 1:
        body, chosen = sorted_bodies[0]
        result_bodies = (_make_witness(1, body, chosen, fragments),)
        status = "UNIQUE"
    else:
        result_bodies = tuple(
            _make_witness(rank, body, chosen, fragments)
            for rank, (body, chosen) in enumerate(sorted_bodies[:2], start=1)
        )
        status = "AMBIGUOUS"

    return ReconstructionResult(
        status=status,
        target_length=length,
        total_weight=best_weight,
        fragment_count=best_count,
        bodies=result_bodies,
        conflicts=tuple(conflicts),
        conflict_positions=tuple(conflict_positions),
        uncovered_positions=(),
        impossible_reason=None,
    )


# --------------------------------------------------------------------------- #
# 证据隔离规划
# --------------------------------------------------------------------------- #


def _mask_to_ids(mask: int, fragments: list[Fragment]) -> tuple[str, ...]:
    return tuple(
        fragments[k].id for k in range(len(fragments)) if mask & (1 << k)
    )


def _inclusion_minimal(sets: list[int]) -> list[int]:
    """剔除被另一个集合包含的位掩码集合(保留极小元)。

    击中集问题中若 T1 ⊆ T2, 任何击中 T1 的方案也击中 T2, 故 T2 是冗余约束。
    """
    uniq = list(set(sets))
    minimal: list[int] = []
    for i, s in enumerate(uniq):
        if any(j != i and uniq[j] and (uniq[j] & s) == uniq[j] for j in range(len(uniq))):
            continue
        minimal.append(s)
    return minimal


def plan_isolation(
    length: int,
    fragments: list[Fragment],
    selected_hex: str,
) -> IsolationPlan:
    """为 AMBIGUOUS 重建中的指定正文生成最优证据隔离方案。

    调用方需保证: 输入合法(无缺口)、原重建裁决为 AMBIGUOUS, 且 selected_hex
    是原重建的某个最优正文(十六进制, 大小写不敏感)。本函数不再重复这些校验。

    关键观察(权重恒正): 给定正文 B 的最优见证 W_B 是强制的——凡与 B 逐字节
    一致(或区间不相交)的片段必被全部采用, 与 B 分歧的片段必被排除; 且
    w(W_B) 恒等于原最优总权重 W*。因此:
      * 只需隔离"与 B 分歧"的片段, 它们一律不在 W_B 中; 隔离它们不会削弱 B;
      * 隔离 Q 后 B 唯一最优, 当且仅当 Q 击中除 B 外每个最优正文的见证
        (即阻断所有产生竞争正文的可行覆盖)。
    于是规划等价于在竞争正文的见证集合上求最小击中集, 排序键为
    (片段数, 权重和, 编号升序列表字典序)。
    """

    n = len(fragments)
    selected = bytes.fromhex(selected_hex)

    enumeration = _enumerate_optimal(length, fragments)
    # 调用契约保证为 AMBIGUOUS, 这里仅做防御性处理。
    assert enumeration is not None, "隔离规划要求原重建存在最优可行覆盖"
    best_weight, best_count, bodies = enumeration
    assert selected in bodies, "所选正文必须是原重建的最优正文之一"

    selected_witness = bodies[selected]
    competitor_bodies = sorted(b for b in bodies if b != selected)

    # 候选隔离片段: 与所选正文分歧者(属于 B 见证的片段必不考虑: 隔离它会削弱 B)。
    candidate_bits = 0
    for k, frag in enumerate(fragments):
        if selected_witness & (1 << k):
            continue
        for i, byte in enumerate(frag.payload):
            if selected[frag.offset + i] != byte:
                candidate_bits |= 1 << k
                break
    cand_indices = [
        k for k in range(n) if candidate_bits & (1 << k)
    ]

    # 每个竞争最优正文的见证集合(只用候选位表示): 隔离方案必须逐一击中。
    target_sets = [
        bodies[body] & candidate_bits for body in competitor_bodies
    ]

    # 仅保留包含意义下极小的目标: 若 T1 ⊆ T2, 击中 T1 必击中 T2, T2 无需再考虑。
    # 目标数在极端实例(如 2^14 个最优正文)下很大, 极小化后通常急剧收缩。
    target_sets = _inclusion_minimal(target_sets)

    cand_weight = {k: fragments[k].weight for k in cand_indices}

    def key_of(chosen_bits: int) -> tuple[int, int, tuple[str, ...]]:
        ids = tuple(
            sorted(fragments[k].id for k in cand_indices if chosen_bits & (1 << k))
        )
        return (len(ids), sum(cand_weight[k] for k in cand_indices if chosen_bits & (1 << k)), ids)

    best_key: Optional[tuple[int, int, tuple[str, ...]]] = None
    best_q = 0

    def packing_lb(targets: list[int], available: int) -> int:
        """还需选取的片段数下界: 贪心收集彼此在 available 上不相交的目标。

        这些目标两两不能由同一候选击中, 故任一击中集至少需要这么多片段。
        """
        pending = list(targets)
        count = 0
        while pending:
            # 每次取可选候选最少的目标, 再剔除与其共享候选的目标。
            pivot = min(pending, key=lambda t: (t & available).bit_count())
            pivot_bits = pivot & available
            count += 1
            pending = [t for t in pending if not (t & available & pivot_bits)]
        return count

    def search(chosen_bits: int, available: int, targets: list[int]) -> None:
        nonlocal best_key, best_q

        # 丢弃已被击中的目标。
        targets = [t for t in targets if not (t & chosen_bits)]
        if not targets:
            key = key_of(chosen_bits)
            if best_key is None or key < best_key:
                best_key, best_q = key, chosen_bits
            return

        # 必选传播: 某目标在当前可用候选中只剩一个选择, 该片段必选。
        forced = 0
        while True:
            singleton = 0
            for t in targets:
                avail = t & available
                if avail == 0:
                    return  # 存在再也无法击中的目标 -> 死枝。
                if avail & (avail - 1) == 0:
                    singleton = avail
                    break
            if not singleton:
                break
            forced |= singleton
            available &= ~singleton
            targets = [t for t in targets if not (t & singleton)]
            if not targets:
                q = chosen_bits | forced
                key = key_of(q)
                if best_key is None or key < best_key:
                    best_key, best_q = key, q
                return
        chosen_bits |= forced
        chosen_count = chosen_bits.bit_count()
        chosen_weight = sum(
            cand_weight[k] for k in cand_indices if chosen_bits & (1 << k)
        )

        # 乐观下界: 完成方案至少还需 lb_extra 个片段。
        lb_extra = packing_lb(targets, available)
        if best_key is not None:
            best_count = best_key[0]
            if chosen_count + lb_extra > best_count:
                return  # 片段数上已不可能优于已知最优。
            if chosen_count + lb_extra == best_count:
                # 即便在"恰好追平片段数"的最好情形, 权重和也已更高 -> 剪枝。
                smallest = sorted(
                    cand_weight[k]
                    for k in cand_indices
                    if available & (1 << k)
                )[:lb_extra]
                if len(smallest) < lb_extra:
                    return
                if chosen_weight + sum(smallest) > best_key[1]:
                    return
                # 权重和可能持平: 仍需继续探索以比较编号列表字典序。

        # MRV: 选可用候选最少的目标分支; 任一可行方案必包含其中恰好某个候选。
        pivot = min(targets, key=lambda t: (t & available).bit_count())
        branch_bits = pivot & available
        branches = [k for k in cand_indices if branch_bits & (1 << k)]
        # (权重, 编号) 升序: 让更优的同级方案先出现, 强化剪枝。
        branches.sort(key=lambda k: (cand_weight[k], fragments[k].id))
        for k in branches:
            bit = 1 << k
            search(chosen_bits | bit, available & ~bit, targets)

    search(0, candidate_bits, target_sets)
    assert best_q, "AMBIGUOUS 实例必然存在非空隔离方案"

    isolated_indices = sorted(
        (k for k in cand_indices if best_q & (1 << k)),
        key=lambda k: fragments[k].id,
    )
    isolated_ids = tuple(fragments[k].id for k in isolated_indices)
    isolated_weight = sum(cand_weight[k] for k in isolated_indices)

    # 隔离后重算: 仅保留不在 Q 中的片段。
    retained = [frag for k, frag in enumerate(fragments) if not (best_q & (1 << k))]
    retained_enum = _enumerate_optimal(length, retained)
    assert retained_enum is not None, "所选正文的见证不被隔离, 重算必可行"
    rec_weight, rec_count, rec_bodies = retained_enum
    rec_sorted = sorted(rec_bodies.items())
    assert len(rec_sorted) == 1 and rec_sorted[0][0] == selected, (
        "内部错误: 隔离方案未能使所选正文唯一"
    )
    rec_witness_mask_retained = rec_sorted[0][1]
    rec_witness_ids = tuple(
        retained[k].id
        for k in range(len(retained))
        if rec_witness_mask_retained & (1 << k)
    )

    # 逐项反例: 单独恢复该片段(其余隔离项仍撤下), 重算须不再 UNIQUE,
    # 并给出字节序最小的竞争正文。
    items: list[IsolatedFragment] = []
    for k in isolated_indices:
        # 单独恢复片段 k: 其余隔离项仍撤下, 并保持原始片段顺序。
        restored_frags = [
            frag for j, frag in enumerate(fragments)
            if not (best_q & (1 << j)) or j == k
        ]
        rest_enum = _enumerate_optimal(length, restored_frags)
        assert rest_enum is not None, "恢复一个片段不可能使原可行覆盖变为不可行"
        rw, rc, rbodies = rest_enum
        competitors = sorted(b for b in rbodies if b != selected)
        if competitors:
            verdict = "AMBIGUOUS"
            comp_body = competitors[0]
            comp_ids = _mask_to_ids(rbodies[comp_body], restored_frags)
            comp_total: Optional[int] = rw
            comp_count: Optional[int] = rc
        else:
            # 仅当恢复后只剩所选正文一个最优正文时才会出现: 这与 q 的必要性
            # 矛盾, 理论上不会发生(击中集最小性保证), 防御性标注。
            verdict = "UNIQUE"
            comp_body = None
            comp_ids = ()
            comp_total, comp_count = rw, rc
        frag = fragments[k]
        items.append(
            IsolatedFragment(
                id=frag.id,
                offset=frag.offset,
                payload_hex=frag.payload.hex().upper(),
                weight=frag.weight,
                restored_verdict=verdict,
                competitor_hex=comp_body.hex().upper() if comp_body is not None else None,
                competitor_witness_fragment_ids=comp_ids,
                restored_total_weight=comp_total,
                restored_fragment_count=comp_count,
            )
        )

    return IsolationPlan(
        selected_hex=selected.hex().upper(),
        isolated_fragment_ids=isolated_ids,
        isolated_weight=isolated_weight,
        retained_fragment_count=len(retained),
        recomputed_total_weight=rec_weight,
        recomputed_fragment_count=rec_count,
        witness_fragment_ids=rec_witness_ids,
        items=tuple(items),
        competing_body_count=len(bodies),
    )


def _mask_to_ids_in(mask: int, fragments: list[Fragment]) -> tuple[str, ...]:
    return _mask_to_ids(mask, fragments)
