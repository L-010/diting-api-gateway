"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Activity, FileSearch, GitBranch, PlugZap, RefreshCw, ShieldAlert } from "lucide-react";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { ToolView, statusLabel } from "@/lib/admin-tools";
import { loadAdminTools, peekAdminTools } from "@/lib/admin-tools-cache";
import { notifyToast } from "@/lib/toast";

export default function AdminToolsPage() {
  const [tools, setTools] = useState<ToolView[] | null>(peekAdminTools);
  const [loading, setLoading] = useState(tools === null);
  const [selectedId, setSelectedId] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState<"" | "health" | "sync">("");
  const toolItems = tools ?? [];
  const selected = toolItems.find((item) => item.id === selectedId) ?? null;

  async function loadTools() {
    const items = await loadAdminTools(true);
    setTools(items);
    setSelectedId((current) => items.some((item) => item.id === current) ? current : "");
  }

  async function refreshAll(showToast = false) {
    setError("");
    setLoading(true);
    try {
      await loadTools();
      if (showToast) notifyToast({ type: "success", message: "工具列表已刷新" });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      if (showToast) notifyToast({ type: "error", message });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshAll();
  }, []);

  async function testHealth() {
    if (!selected) return;
    setBusyAction("health");
    setError("");
    setMessage("");
    try {
      const result = await api<{ status: string; status_code?: number | null }>(`/api/admin/tools/${selected.id}/health/test`, { method: "POST" });
      const message = `连接测试完成：${result.status}，状态码 ${result.status_code ?? "无响应"}`;
      setMessage(message);
      notifyToast({ type: result.status === "ok" ? "success" : "info", message });
      await refreshAll();
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setBusyAction("");
    }
  }

  async function syncOpenapi() {
    if (!selected) return;
    setBusyAction("sync");
    setError("");
    setMessage("");
    try {
      const result = await api<{ batch: { total_operations: number; blocked_count: number; excluded_count: number } }>(`/api/admin/tools/${selected.id}/sync-diff`, { method: "POST" });
      const message = `同步完成：发现 ${result.batch.total_operations} 个操作，阻断 ${result.batch.blocked_count} 个，默认排除 ${result.batch.excluded_count} 个。`;
      setMessage(message);
      notifyToast({ type: "success", message });
      await refreshAll();
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setBusyAction("");
    }
  }

  const totals = {
    endpoints: toolItems.reduce((sum, item) => sum + item.endpoint_count, 0),
    published: toolItems.reduce((sum, item) => sum + item.published_count, 0),
    blocked: toolItems.reduce((sum, item) => sum + item.blocked_count, 0),
    reachable: toolItems.filter((item) => item.health?.reachable).length,
  };

  return (
    <PortalShell admin title="工具/API 接入工作台">
      <p className="page-intro">
        从这里新建工具或进入已有工具工作台。接口导入、访问策略判断和路由发布均归属于对应工具。
      </p>
      <div className={`metrics${tools === null ? " metrics-loading" : ""}`} aria-busy={loading}>
        <Metric label="工具总数" value={tools === null ? "-" : String(toolItems.length)} caption="上游工具" />
        <Metric label="接口总数" value={tools === null ? "-" : String(totals.endpoints)} caption="候选、排除、阻断与已发布" tone="green" />
        <Metric label="已发布路由" value={tools === null ? "-" : String(totals.published)} caption="可被 X-API-Key 调用" tone="purple" />
        <Metric label="风险阻断" value={tools === null ? "-" : String(totals.blocked)} caption={tools === null ? "正在读取最近状态" : `上游可达 ${totals.reachable}/${toolItems.length}`} tone={totals.blocked ? "red" : "amber"} />
      </div>

      <div className="toolbar" style={{ marginTop: 24 }}>
        <div className="toolbar-actions">
          <Link href="/admin/tools/new" className="button">
            <PlugZap size={16} />
            新建工具向导
          </Link>
          {selected && (
            <>
              <span className="selected-tool-target">当前目标：{selected.name}</span>
              <Link href={`/admin/tools/${selected.id}/import`} className="button secondary">
                <FileSearch size={16} />
                导入 OpenAPI/Swagger
              </Link>
              <Link href={`/admin/tools/${selected.id}/diff`} className="button secondary">
                <GitBranch size={16} />
                同步差异
              </Link>
            </>
          )}
        </div>
        <button className="icon-text-button" onClick={() => void refreshAll(true)} disabled={Boolean(busyAction)}>
          <RefreshCw size={16} />
          刷新
        </button>
      </div>

      {(message || error) && <p className={`form-message ${message ? "success" : ""}`}>{message || error}</p>}

      <div className="admin-layout">
        <section className="panel">
          <div className="toolbar">
            <h2>工具列表</h2>
            <span className="muted">slug 创建后不可修改；Token 只可替换、不回显。</span>
          </div>
          <div className="table-wrap" aria-busy={loading}>
            <table className="data-table">
              <thead>
                <tr>
                  <th className="tool-select-column">选择</th>
                  <th>工具</th>
                  <th>状态</th>
                  <th>接口治理</th>
                  <th>上游</th>
                  <th>最近导入</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {toolItems.map((tool) => (
                  <tr key={tool.id} className={selected?.id === tool.id ? "selected-row" : undefined} aria-selected={selected?.id === tool.id}>
                    <td className="tool-select-column">
                      <input
                        type="radio"
                        name="selected-tool"
                        value={tool.id}
                        checked={selected?.id === tool.id}
                        onChange={() => setSelectedId(tool.id)}
                        aria-label={`选择工具 ${tool.name}`}
                      />
                    </td>
                    <td className="title-cell">
                      <strong>{tool.name}</strong>
                      <span className="muted">
                        /gateway/{tool.slug}
                        <br />
                        {tool.description || "暂无说明"}
                      </span>
                    </td>
                    <td>
                      <span className={tool.is_enabled ? "status good" : "status warn"}>{statusLabel(tool.status)}</span>
                      <div className="muted">策略待确认：{Number(tool.onboarding?.pending_access_policy_count || 0)}</div>
                    </td>
                    <td>
                      已发布 {tool.published_count} / 草稿 {tool.draft_count}
                      <br />
                      <span className="muted">排除 {tool.excluded_count}，阻断 {tool.blocked_count}</span>
                    </td>
                    <td>
                      {tool.base_url_masked}
                      <br />
                      <span className={tool.health?.status === "unknown" ? "status info" : tool.health?.reachable ? "status good" : "status warn"}>
                        {tool.health?.status === "unknown" ? "未检测" : tool.health?.reachable ? "可达" : "不可用"}
                      </span>
                    </td>
                    <td>
                      {tool.latest_openapi_imported_at ? new Date(tool.latest_openapi_imported_at).toLocaleString() : "尚未导入"}
                      <br />
                      <span className="muted">{tool.latest_openapi_sha256 ? `${tool.latest_openapi_sha256.slice(0, 12)}...` : "无文档摘要"}</span>
                    </td>
                    <td>
                      <Link href={`/admin/tools/${tool.id}`} className="button secondary compact" onClick={(event) => event.stopPropagation()}>
                        进入工作台
                      </Link>
                    </td>
                  </tr>
                ))}
                {tools === null && (
                  <tr>
                    <td colSpan={7}>
                      <div className="table-loading-state" role="status">正在加载工具数据...</div>
                    </td>
                  </tr>
                )}
                {tools !== null && !tools.length && (
                  <tr>
                    <td colSpan={7}>
                      <div className="state-box">尚未配置工具。请从“新建工具向导”开始。</div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <aside className="panel">
          <Activity size={24} color="#1a5ec0" />
          <h2 style={{ marginTop: 12 }}>当前工具概览</h2>
          {selected ? (
            <>
              <div className="signal-card neutral">
                <strong>{selected.name}</strong>
                <p className="muted">
                  Gateway：/gateway/{selected.slug}
                  <br />
                  上游：{selected.base_url_masked}
                  <br />
                  Token：{selected.token_configured ? "已配置（脱敏）" : "未配置"}
                </p>
              </div>
              <div className="step-list" style={{ marginTop: 14 }}>
                <div className="step-item active">
                  <span className="step-index">1</span>
                  <div>
                    <strong>连接测试</strong>
                    <small>{selected.health_path}，不跟随重定向</small>
                  </div>
                </div>
                <div className="step-item">
                  <span className="step-index">2</span>
                  <div>
                    <strong>导入规范</strong>
                    <small>URL / 文件 / 文本粘贴</small>
                  </div>
                </div>
                <div className="step-item">
                  <span className="step-index">3</span>
                  <div>
                    <strong>治理发布</strong>
                    <small>阻断风险、确认原因、审计留痕</small>
                  </div>
                </div>
              </div>
              <button className="button" style={{ width: "100%", marginTop: 16 }} onClick={() => void testHealth()} disabled={Boolean(busyAction)}>
                {busyAction === "health" ? "测试中" : "连接测试"}
              </button>
              <button className="button secondary" style={{ width: "100%", marginTop: 10 }} onClick={() => void syncOpenapi()} disabled={Boolean(busyAction)}>
                {busyAction === "sync" ? "同步中" : "从上游同步差异"}
              </button>
            </>
          ) : (
            <div className="state-box">请选择一个工具。</div>
          )}
          <p className="notice" style={{ marginTop: 16 }}>
            <ShieldAlert size={15} style={{ verticalAlign: "middle", marginRight: 6 }} />
            所有工具统一使用接口访问策略。
          </p>
        </aside>
      </div>

    </PortalShell>
  );
}
