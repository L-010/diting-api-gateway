"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, GitBranch, RefreshCw, ShieldX } from "lucide-react";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { AccessMode, ImportBatchView, OpenApiDiffView, accessModeLabel, diffLabel, riskLabel } from "@/lib/admin-tools";
import { notifyToast } from "@/lib/toast";

type SyncResult = { batch: ImportBatchView | null; diffs: OpenApiDiffView[] };
type BulkDecisionResult = { updated: OpenApiDiffView[]; failed: Array<{ diff_id: string; reason: string }> };

export default function ToolDiffPage() {
  const params = useParams<{ toolId: string }>();
  const toolId = params.toolId;
  const [batch, setBatch] = useState<ImportBatchView | null>(null);
  const [diffs, setDiffs] = useState<OpenApiDiffView[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [auditReason, setAuditReason] = useState("接口治理与原子发布确认");
  const [sharedConfirmed, setSharedConfirmed] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loadingLatest, setLoadingLatest] = useState(true);
  const selected = useMemo(() => diffs.find((item) => item.id === selectedId) ?? diffs[0] ?? null, [diffs, selectedId]);

  useEffect(() => {
    let cancelled = false;
    api<SyncResult>(`/api/admin/tools/${toolId}/import-batches/latest`)
      .then((result) => {
        if (cancelled || !result.batch) return;
        setBatch(result.batch);
        setDiffs(result.diffs);
        setSelectedId(result.diffs[0]?.id || "");
      })
      .catch((cause) => {
        if (!cancelled) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setLoadingLatest(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toolId]);

  async function sync() {
    setMessage("");
    setError("");
    try {
      const result = await api<SyncResult>(`/api/admin/tools/${toolId}/sync-diff`, { method: "POST" });
      if (!result.batch) throw new Error("同步接口未返回批次信息");
      setBatch(result.batch);
      setDiffs(result.diffs);
      setSelectedId(result.diffs[0]?.id || "");
      setSelectedIds([]);
      const message = "同步差异已生成，请处理阻断、删除和权限风险。";
      setMessage(message);
      notifyToast({ type: "success", message });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function decide(diff: OpenApiDiffView, decision: "accept" | "exclude" | "defer" | "block") {
    if (["exclude", "block"].includes(decision) && !window.confirm(`${decision === "exclude" ? "排除" : "阻断"}接口“${diff.summary || diff.gateway_path}”？此状态不会进入发布候选。`)) return;
    setMessage("");
    setError("");
    try {
      let reason = "管理员处理工具同步差异";
      let accessPolicy: Record<string, unknown> | undefined;
      if (decision === "accept" && !diff.access_policy_suggestion.complete) {
        if (diff.access_policy_suggestion.access_mode !== "shared" || !sharedConfirmed || !auditReason.trim()) {
          const message = "共享数据必须勾选共享确认并填写审计原因。";
          setError(message);
          notifyToast({ type: "error", message });
          return;
        }
        reason = auditReason.trim();
        accessPolicy = { access_mode: "shared", rules: diff.access_policy_suggestion.rules, shared_confirm: true };
      }
      const updated = await api<OpenApiDiffView>(`/api/admin/diffs/${diff.id}/decision`, {
        method: "PATCH",
        body: JSON.stringify({ decision, reason, access_policy: accessPolicy, use_suggestion: true }),
      });
      setDiffs((rows) => rows.map((item) => (item.id === updated.id ? updated : item)));
      const message = "差异状态已更新。";
      setMessage(message);
      notifyToast({ type: "success", message });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function bulkDecide(decision: "accept" | "block", accessMode?: AccessMode) {
    if (!selectedIds.length) return;
    const actionLabel = decision === "accept" ? accessMode === "shared" ? "批量设为共享" : "批量采用访问建议" : "批量阻断";
    if (!window.confirm(`${actionLabel} ${selectedIds.length} 个差异接口？此操作会改变后续治理状态。`)) return;
    const reason = auditReason.trim() || `管理员批量${decision === "accept" ? "放行" : "阻断"}接口`;
    if (accessMode === "shared" && (!sharedConfirmed || !auditReason.trim())) {
      const message = "批量设为共享前必须勾选共享确认并填写审计原因。";
      setError(message);
      notifyToast({ type: "error", message });
      return;
    }
    setMessage("");
    setError("");
    try {
      const result = await api<BulkDecisionResult>("/api/admin/diffs/bulk-decision", {
        method: "POST",
        body: JSON.stringify({
          diff_ids: selectedIds,
          decision,
          reason,
          access_policy: decision === "accept" && accessMode ? { access_mode: accessMode, rules: { version: 1, pre_checks: [], post_actions: [], list_filter: null }, shared_confirm: accessMode === "shared" } : undefined,
          use_suggestions: !accessMode,
        }),
      });
      setDiffs((rows) => rows.map((item) => result.updated.find((updated) => updated.id === item.id) ?? item));
      setSelectedIds([]);
      if (result.failed.length) {
        const message = `已处理 ${result.updated.length} 个接口，${result.failed.length} 个策略不完整：${result.failed[0].reason}`;
        setError(message);
        notifyToast({ type: "error", message });
      } else {
        const message = `已批量处理 ${result.updated.length} 个接口。`;
        setMessage(message);
        notifyToast({ type: "success", message });
      }
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function publishAccepted() {
    if (!window.confirm("将原子发布当前批次中已放行的接口；阻断、排除和策略不完整的接口不会发布。是否继续？")) return;
    const reason = auditReason.trim() || "管理员确认原子发布全部放行接口";
    setMessage("");
    setError("");
    try {
      const result = await api<{ published_count: number; skipped_count: number }>(`/api/admin/tools/${toolId}/endpoints/bulk-publish`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, reason }),
      });
      const message = `已原子发布全部 ${result.published_count} 个放行接口。`;
      setMessage(message);
      notifyToast({ type: "success", message });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function confirmBatch() {
    if (!batch) return;
    const reason = auditReason.trim() || "管理员确认导入批次";
    setMessage("");
    setError("");
    try {
      const updated = await api<ImportBatchView>(`/api/admin/import-batches/${batch.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, reason }),
      });
      setBatch(updated);
      const message = "导入批次已确认，可以一次性发布全部放行接口。";
      setMessage(message);
      notifyToast({ type: "success", message });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  const counts = {
    new: diffs.filter((item) => item.diff_type === "new").length,
    changed: diffs.filter((item) => item.diff_type === "changed").length,
    deleted: diffs.filter((item) => item.diff_type === "deleted").length,
    blocked: diffs.filter((item) => item.risk_level === "blocker").length,
  };
  const governanceSummary = batch?.governance_summary || {};
  const pendingDecisionCount = diffs.filter((item) => item.decision === "pending").length;
  const blockedCount = Number(governanceSummary.blocked_count || counts.blocked);
  const excludedCount = diffs.filter((item) => item.decision === "exclude" || item.decision === "block").length;
  const acceptedCount = diffs.filter((item) => item.decision === "accept").length;

  return (
    <PortalShell admin title="OpenAPI 同步差异决策">
      <p className="page-intro">
        用于处理上游规范更新后的新增、变更、删除和风险变化。同步生成差异不会自动发布路由；管理员可逐条快速变更状态。
      </p>

      <div className="toolbar">
        <div className="toolbar-actions">
          <button className="button" onClick={() => void sync()}>
            <RefreshCw size={16} />
            拉取上游并生成差异
          </button>
          <Link href={`/admin/tools/${toolId}`} className="button secondary">
            返回工具工作台
          </Link>
          <button className="button secondary" disabled={!batch || batch.status === "applied" || pendingDecisionCount > 0} title={pendingDecisionCount ? `仍有 ${pendingDecisionCount} 个接口待决策` : undefined} onClick={() => void confirmBatch()}>
            <CheckCircle2 size={16} />
            确认导入批次
          </button>
          <button className="button secondary" disabled={!batch || batch.status !== "applied"} onClick={() => void publishAccepted()}>
            一键发布全部放行接口
          </button>
        </div>
        <span className="muted">路径冲突、删除接口、内部接口暴露必须阻断。</span>
      </div>
      {(message || error) && <p className={`form-message ${message ? "success" : ""}`}>{message || error}</p>}
      <div className="inline-form" style={{ marginTop: 16 }}>
        <label className="form-field">
          本次治理审计原因
          <input value={auditReason} onChange={(event) => setAuditReason(event.target.value)} placeholder="说明本次接口判断与发布原因" />
        </label>
        <label className="form-field checkbox-field">
          <input type="checkbox" checked={sharedConfirmed} onChange={(event) => setSharedConfirmed(event.target.checked)} />
          已确认共享接口会向全部已审批用户返回全量数据
        </label>
      </div>

      <div className="metrics">
        <Metric label="新增" value={String(counts.new)} caption="候选新接口" tone="green" />
        <Metric label="变更" value={String(counts.changed)} caption="需结构复核" tone="amber" />
        <Metric label="删除" value={String(counts.deleted)} caption="上游已移除" tone="purple" />
        <Metric label="阻断" value={String(counts.blocked)} caption="不可发布" tone={counts.blocked ? "red" : "blue"} />
      </div>

      <div className="metrics" style={{ marginTop: 14 }}>
        <Metric label="待决策" value={String(pendingDecisionCount)} caption="确认批次前应处理" tone={pendingDecisionCount ? "amber" : "green"} />
        <Metric label="阻断治理" value={String(blockedCount)} caption="必须排除或阻断" tone={blockedCount ? "red" : "blue"} />
        <Metric label="已排除" value={String(excludedCount)} caption="不进入发布候选" tone="purple" />
        <Metric label="已接受" value={String(acceptedCount)} caption="可进入发布治理" tone="green" />
      </div>

      <div className="page-split editor-layout" style={{ marginTop: 24 }}>
        <section className="panel">
          <div className="toolbar">
            <h2>差异项</h2>
            <div className="toolbar-actions" style={{ flexWrap: "wrap" }}>
              <button className="button secondary compact" disabled={!selectedIds.length || batch?.status === "applied"} onClick={() => void bulkDecide("accept")}>批量采用建议</button>
              <button className="button secondary compact" disabled={!selectedIds.length || batch?.status === "applied"} onClick={() => void bulkDecide("accept", "shared")}>批量设为共享</button>
              <button className="button secondary compact" disabled={!selectedIds.length || batch?.status === "applied"} onClick={() => void bulkDecide("accept", "admin_only")}>批量设为仅管理员</button>
              <button className="button secondary compact" disabled={!selectedIds.length || batch?.status === "applied"} onClick={() => void bulkDecide("block")}>批量阻断</button>
              <span className={batch?.status === "applied" ? "status good" : "status warn"}>{batch ? `批次 ${batch.id.slice(0, 8)}` : "未同步"}</span>
            </div>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th><input type="checkbox" checked={diffs.length > 0 && selectedIds.length === diffs.length} onChange={(event) => setSelectedIds(event.target.checked ? diffs.map((item) => item.id) : [])} aria-label="选择全部差异接口" /></th>
                  <th>类型</th>
                  <th>方法</th>
                  <th>Gateway 路径</th>
                  <th>接口作用</th>
                  <th>风险</th>
                  <th>访问策略</th>
                  <th>决策</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {diffs.map((diff) => (
                  <tr key={diff.id} onClick={() => setSelectedId(diff.id)} style={{ cursor: "pointer", outline: selected?.id === diff.id ? "2px solid #1a5ec0" : "none" }}>
                    <td><input type="checkbox" checked={selectedIds.includes(diff.id)} onClick={(event) => event.stopPropagation()} onChange={(event) => setSelectedIds((current) => event.target.checked ? [...new Set([...current, diff.id])] : current.filter((id) => id !== diff.id))} aria-label={`选择 ${diff.gateway_path}`} /></td>
                    <td>{diffLabel(diff.diff_type)}</td>
                    <td>
                      <span className={diff.method === "DELETE" ? "status warn" : "status info"}>{diff.method}</span>
                    </td>
                    <td className="endpoint-path">{diff.gateway_path}</td>
                    <td>
                      <strong>{diff.summary || diff.gateway_path}</strong>
                      <br />
                      <span className="muted">{diff.description || "上游规范未提供 description。"}</span>
                    </td>
                    <td>
                      {riskLabel(diff.risk_level)}
                      <br />
                      <span className="muted">{diff.risk_flags.join(", ") || "无显著风险"}</span>
                    </td>
                    <td>
                      {accessModeLabel(diff.access_policy.access_mode || diff.access_policy_suggestion.access_mode)}
                      <br />
                      <span className={diff.access_policy_complete ? "status good" : "status warn"}>{diff.access_policy_complete ? "已确认" : diff.access_policy_suggestion.reason}</span>
                    </td>
                    <td>{diff.decision}</td>
                    <td>
                      <button className="button secondary compact" disabled={batch?.status === "applied"} onClick={(event) => { event.stopPropagation(); void decide(diff, "accept"); }}>
                        接受
                      </button>
                    </td>
                  </tr>
                ))}
                {!diffs.length && (
                  <tr>
                    <td colSpan={9}>
                      <div className="state-box">{loadingLatest ? "正在读取最近同步批次..." : "尚未生成同步差异。"}</div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <aside className="panel">
          <GitBranch size={25} color="#1a5ec0" />
          <h2 style={{ marginTop: 12 }}>差异详情</h2>
          {selected ? (
            <>
              <div className="signal-card info">
                <strong>
                  {selected.method} {selected.summary || "未命名接口"}
                </strong>
                <p className="endpoint-path">{selected.gateway_path}</p>
                <p className="muted">{selected.description || "上游规范未提供 description。"}</p>
              </div>
              <div className="list-row">
                <span>差异类型</span>
                <strong>{diffLabel(selected.diff_type)}</strong>
              </div>
              <div className="list-row">
                <span>风险等级</span>
                <span className={selected.risk_level === "blocker" ? "status warn" : "status info"}>{riskLabel(selected.risk_level)}</span>
              </div>
              <div className="list-row">
                <span>访问策略</span>
                <strong>{accessModeLabel(selected.access_policy.access_mode || selected.access_policy_suggestion.access_mode)}</strong>
              </div>
              <p className="muted">{selected.access_policy_suggestion.reason}</p>
              <pre className="code-block">{JSON.stringify(selected.detail, null, 2)}</pre>
              <div className="toolbar-actions" style={{ marginTop: 14, flexWrap: "wrap" }}>
                <button className="button compact" onClick={() => void decide(selected, "accept")}>
                  <CheckCircle2 size={15} />
                  接受
                </button>
                <button className="button secondary compact" onClick={() => void decide(selected, "exclude")}>
                  排除
                </button>
                <button className="button secondary compact" onClick={() => void decide(selected, "defer")}>
                  稍后处理
                </button>
                <button className="button secondary compact" onClick={() => void decide(selected, "block")}>
                  <ShieldX size={15} />
                  阻断
                </button>
              </div>
            </>
          ) : (
            <div className="state-box">请选择一个差异项。</div>
          )}
        </aside>
      </div>
    </PortalShell>
  );
}
