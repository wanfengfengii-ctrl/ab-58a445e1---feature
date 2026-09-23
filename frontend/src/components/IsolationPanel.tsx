import { useState } from "react";
import type { Counterexample, IsolationPlan } from "../types";

interface Props {
  plan: IsolationPlan;
  onDismiss: () => void;
}

function CounterexampleRow({ ce }: { ce: Counterexample }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="ce-item">
      <button
        type="button"
        className="ce-head"
        onClick={() => setOpen((v) => !v)}
        title="展开单独恢复该片段后的反例裁决"
      >
        <span className="ce-toggle">{open ? "▾" : "▸"}</span>
        <span className="w-id">{ce.fragment_id}</span>
        <span className="ce-brief">
          恢复后裁决 <strong>AMBIGUOUS</strong> · 竞争正文{" "}
          <span className="mono">{ce.rival_hex}</span>
        </span>
      </button>
      {open && (
        <div className="ce-body">
          <div className="ce-row">
            <span className="ce-label">反例裁决</span>
            <span className="badge ambiguous">{ce.restored_verdict}</span>
            <span className="ce-hint">
              单独恢复该片段并重算, 重新出现与所选正文并列最优的竞争正文,
              证明它在此方案中不可省略。
            </span>
          </div>
          <div className="ce-row">
            <span className="ce-label">所选正文</span>
            <span className="mono">{ce.selected_hex}</span>
          </div>
          <div className="ce-row">
            <span className="ce-label">竞争正文</span>
            <span className="mono rival">{ce.rival_hex}</span>
          </div>
          <div className="ce-row">
            <span className="ce-label">竞争见证</span>
            <span className="ce-wits">
              {ce.rival_witness_fragment_ids.map((id) => (
                <span
                  key={id}
                  className={`wit-chip${id === ce.fragment_id ? " forced" : ""}`}
                  title={
                    id === ce.fragment_id
                      ? "被恢复的被隔离片段 — 竞争正文动员了它"
                      : undefined
                  }
                >
                  {id}
                </span>
              ))}
            </span>
          </div>
          <div className="ce-row">
            <span className="ce-label">并列最优值</span>
            <span className="mono">
              总权重 {ce.optimal.total_weight} · 片段数{" "}
              {ce.optimal.fragment_count}
            </span>
          </div>
        </div>
      )}
    </li>
  );
}

export default function IsolationPanel({ plan, onDismiss }: Props) {
  return (
    <div className="isolation">
      <div className="verdict-banner isolated">
        <h2>证据隔离方案 · 重算后正文唯一</h2>
        <p>
          暂不采用下列片段(内容与权重均不改动), 剩余证据按现有“先总权重、
          后片段数”规则重算后, 唯一得到所选正文。
        </p>
      </div>

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
          <span className="m-value">{plan.optimal.total_weight}</span>
        </div>
        <div className="metric">
          <span className="m-label">重算片段数</span>
          <span className="m-value">{plan.optimal.fragment_count}</span>
        </div>
      </div>

      <div className="notice isolation-note">
        <div className="iso-target">
          <span className="ce-label">所选正文</span>
          <span className="mono iso-target-hex">{plan.selected_hex}</span>
        </div>
      </div>

      <div className="iso-section">
        <h4>
          暂不采用的片段（{plan.isolated_fragments.length}）
          <span className="hint">
            {" "}
            — 已按编号升序排列, 方案在“片段数 → 权重和 → 编号字典序”下最优
          </span>
        </h4>
        <ul className="witness-list iso-list">
          {plan.isolated_fragments.map((fr) => (
            <li key={fr.id} className="iso-frag">
              <span className="w-id">{fr.id}</span>
              <span className="w-off">@{fr.offset}</span>
              <span className="w-hex mono">{fr.payload}</span>
              <span className="w-weight">w={fr.weight}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="iso-section">
        <h4>重算后的唯一正文见证</h4>
        <ul className="witness-list">
          {plan.target_body.adopted_fragments.map((fr) => (
            <li key={fr.id}>
              <span className="w-id">{fr.id}</span>
              <span className="w-off">@{fr.offset}</span>
              <span className="w-hex mono">{fr.payload}</span>
              <span className="w-weight">w={fr.weight}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="iso-section">
        <h4>逐项反例核对（{plan.counterexamples.length}）</h4>
        <p className="hint">
          展开任一项: 单独恢复该片段后的重算裁决与竞争正文。每项反例的竞争
          见证都动员该片段, 证明它不可省略。
        </p>
        <ul className="ce-list">
          {plan.counterexamples.map((ce) => (
            <CounterexampleRow key={ce.fragment_id} ce={ce} />
          ))}
        </ul>
      </div>

      <div className="iso-actions">
        <button type="button" className="btn ghost small" onClick={onDismiss}>
          收起隔离方案
        </button>
      </div>
    </div>
  );
}
