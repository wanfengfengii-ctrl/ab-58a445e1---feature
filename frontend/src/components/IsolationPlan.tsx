import type { IsolationResponse, IsolatedFragment } from "../types";

interface Props {
  response: IsolationResponse | null;
  loading: boolean;
  error: string | null;
  onDismiss: () => void;
}

const VERDICT_CLS: Record<string, string> = {
  UNIQUE: "unique",
  AMBIGUOUS: "ambiguous",
  IMPOSSIBLE: "impossible",
};

function Counterexample({ item }: { item: IsolatedFragment }) {
  return (
    <div className="counter-item">
      <div className="counter-head">
        <span className="w-id">恢复 {item.id}</span>
        <span className={`verdict-tag ${VERDICT_CLS[item.restored_verdict] ?? ""}`}>
          {item.restored_verdict}
        </span>
      </div>
      {item.competitor_hex === null ? (
        <p className="hint">恢复后仍为 UNIQUE（数据异常: 该片段本应可省略）。</p>
      ) : (
        <div className="counter-body">
          <div className="counter-row">
            <span className="counter-label">竞争正文</span>
            <code className="mono">{item.competitor_hex}</code>
          </div>
          <div className="counter-row">
            <span className="counter-label">竞争见证</span>
            <span className="mono">
              {[...item.competitor_witness_fragment_ids]
                .sort()
                .join(", ")}
            </span>
          </div>
          <div className="counter-row">
            <span className="counter-label">反例最优值</span>
            <span className="mono">
              总权重 {item.restored_optimal.total_weight ?? "—"} · 片段数{" "}
              {item.restored_optimal.fragment_count ?? "—"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function IsolationPlan({ response, loading, error, onDismiss }: Props) {
  const plan = response?.plan ?? null;
  return (
    <div className="isolation-panel">
      <div className="isolation-head">
        <h3>证据隔离方案</h3>
        <button type="button" className="btn ghost small" onClick={onDismiss}>
          撤下方案
        </button>
      </div>

      {loading && <p className="hint">正在比较全部可行最优覆盖…</p>}
      {error && <p className="isolation-error">{error}</p>}

      {!loading && !error && plan && (
        <>
          <p className="hint">
            在不改动任何片段内容与权重的前提下, 暂不采用下列片段后, 剩余证据按
            “先总权重、后片段数”重算将唯一得到所选正文{" "}
            <code className="mono">{plan.selected_hex}</code>。方案在比较全部{" "}
            {plan.competing_body_count} 份可行最优正文后得出, 非仅当前展示的两份见证。
          </p>

          <div className="metrics">
            <div className="metric">
              <span className="m-label">隔离片段数</span>
              <span className="m-value">{plan.isolated_fragment_ids.length}</span>
            </div>
            <div className="metric">
              <span className="m-label">隔离权重总和</span>
              <span className="m-value">{plan.isolated_weight}</span>
            </div>
            <div className="metric">
              <span className="m-label">重算总权重</span>
              <span className="m-value">
                {plan.recomputed_optimal.total_weight}
              </span>
            </div>
            <div className="metric">
              <span className="m-label">重算片段数</span>
              <span className="m-value">
                {plan.recomputed_optimal.fragment_count}
              </span>
            </div>
          </div>

          <div className="iso-section">
            <div className="iso-section-title">
              隔离项核对（{plan.isolated_fragment_ids.length}）
            </div>
            <ul className="iso-list">
              {plan.isolated_fragments.map((it) => (
                <li key={it.id}>
                  <span className="iso-check" title="暂不采用">✕</span>
                  <span className="w-id">{it.id}</span>
                  <span className="w-off">@{it.offset}</span>
                  <span className="w-hex mono">{it.payload}</span>
                  <span className="w-weight">w={it.weight}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="iso-section">
            <div className="iso-section-title">
              重算见证（{plan.witness_fragment_ids.length}）
            </div>
            <p className="mono iso-witness">
              {[...plan.witness_fragment_ids].sort().join(", ")}
            </p>
          </div>

          <div className="iso-section">
            <div className="iso-section-title">
              逐项反例（证明每个隔离项不可省略）
            </div>
            <p className="hint">
              单独恢复任一隔离项(其余隔离项仍撤下), 所选正文立即重新失去唯一最优
              地位, 并给出字节序最小的竞争正文。
            </p>
            <div className="counter-grid">
              {plan.isolated_fragments.map((it) => (
                <Counterexample key={it.id} item={it} />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
