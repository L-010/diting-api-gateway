"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useDeferredValue, useEffect, useState } from "react";
import { Activity, ArrowRight, ChevronLeft, ChevronRight, FileSearch, GitBranch, KeyRound, RefreshCw, Route, Search, Settings2, ShieldCheck, X } from "lucide-react";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { EndpointView, ToolView, accessModeLabel, riskLabel, statusLabel } from "@/lib/admin-tools";
import { notifyToast } from "@/lib/toast";

type BulkPublishResult = {
  published_count: number;
  skipped_count: number;
  skipped: Array<{ endpoint_id: string; method: string; gateway_path: string; reason: string }>;
};

type EndpointCatalogPage = {
  items: EndpointView[];
  total: number;
  page: number;
  page_size: number;
  page_count: number;
  methods: string[];
  groups: string[];
};

const emptyCatalog: EndpointCatalogPage = {
  items: [],
  total: 0,
  page: 1,
  page_size: 50,
  page_count: 1,
  methods: [],
  groups: [],
};

function formatBytes(value: number) {
  if (!value) return "未设置";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

export default function ToolDetailPage() {
  const params = useParams<{ toolId: string }>();
  const toolId = params.toolId;
  const [tool, setTool] = useState<ToolView | null>(null);
  const [catalog, setCatalog] = useState<EndpointCatalogPage>(emptyCatalog);
  const [searchInput, setSearchInput] = useState("");
  const deferredSearch = useDeferredValue(searchInput.trim());
  const [statusFilter, setStatusFilter] = useState("");
  const [methodFilter, setMethodFilter] = useState("");
  const [riskFilter, setRiskFilter] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [busyEndpointId, setBusyEndpointId] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [taskAdapterText, setTaskAdapterText] = useState("{}");
  const [taskAdapterReason, setTaskAdapterReason] = useState("");
  const [adapterStatusEndpoint, setAdapterStatusEndpoint] = useState("");
  const [adapterLogsEndpoint, setAdapterLogsEndpoint] = useState("");
  const [adapterManifestEndpoint, setAdapterManifestEndpoint] = useState("");
  const [adapterArtifactsEndpoint, setAdapterArtifactsEndpoint] = useState("");
  const [adapterBundleEndpoint, setAdapterBundleEndpoint] = useState("");
  const [adapterDownloadEndpoint, setAdapterDownloadEndpoint] = useState("");
  const [adapterDeleteEndpoint, setAdapterDeleteEndpoint] = useState("");
  const [adapterStorageEndpoint, setAdapterStorageEndpoint] = useState("");
  const [adapterEndpoints, setAdapterEndpoints] = useState<EndpointView[]>([]);

  const loadTool = useCallback(async () => {
    const next = await api<ToolView>(`/api/admin/tools/${toolId}`);
    setTool(next);
    setTaskAdapterText(JSON.stringify(next.task_adapter || {}, null, 2));
  }, [toolId]);

  const loadCatalog = useCallback(async (signal?: AbortSignal) => {
    setCatalogLoading(true);
    const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (deferredSearch) query.set("search", deferredSearch);
    if (statusFilter) query.set("status", statusFilter);
    if (methodFilter) query.set("method", methodFilter);
    if (riskFilter) query.set("risk_level", riskFilter);
    if (groupFilter) query.set("group_name", groupFilter);
    try {
      const nextCatalog = await api<EndpointCatalogPage>(`/api/admin/tools/${toolId}/endpoint-catalog?${query}`, { signal });
      setCatalog(nextCatalog);
      if (nextCatalog.page !== page) setPage(nextCatalog.page);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(errorMessage(cause));
    } finally {
      if (!signal?.aborted) setCatalogLoading(false);
    }
  }, [deferredSearch, groupFilter, methodFilter, page, pageSize, riskFilter, statusFilter, toolId]);

  const loadAdapterEndpoints = useCallback(async () => {
    setAdapterEndpoints(await api<EndpointView[]>(`/api/admin/tools/${toolId}/endpoints`));
  }, [toolId]);

  useEffect(() => {
    loadTool().catch((cause) => setError(errorMessage(cause)));
    loadAdapterEndpoints().catch((cause) => setError(errorMessage(cause)));
  }, [loadAdapterEndpoints, loadTool]);

  useEffect(() => {
    setPage(1);
  }, [deferredSearch, groupFilter, methodFilter, pageSize, riskFilter, statusFilter]);

  useEffect(() => {
    const controller = new AbortController();
    void loadCatalog(controller.signal);
    return () => controller.abort();
  }, [loadCatalog]);

  async function refreshWorkbench(showToast = false) {
    setError("");
    try {
      await Promise.all([loadTool(), loadCatalog(), loadAdapterEndpoints()]);
      if (showToast) notifyToast({ type: "success", message: "接口目录已刷新" });
    } catch (cause) {
      const nextError = errorMessage(cause);
      setError(nextError);
      if (showToast) notifyToast({ type: "error", message: nextError });
    }
  }

  async function healthTest() {
    setBusyAction("health");
    setError("");
    setMessage("");
    try {
      const result = await api<{ status: string; status_code?: number | null }>(`/api/admin/tools/${toolId}/health/test`, { method: "POST" });
      const nextMessage = `健康检查完成：${result.status}，状态码 ${result.status_code ?? "无响应"}`;
      setMessage(nextMessage);
      notifyToast({ type: result.status === "ok" ? "success" : "info", message: nextMessage });
      await loadTool();
    } catch (cause) {
      const nextError = errorMessage(cause);
      setError(nextError);
      notifyToast({ type: "error", message: nextError });
    } finally {
      setBusyAction("");
    }
  }

  async function bulkPublishSafeEndpoints() {
    if (busyAction) return;
    const confirmed = window.confirm("将发布当前工具中已通过访问策略、且风险不高于中风险的接口；排除、阻断和高风险接口不会发布。是否继续？");
    if (!confirmed) return;
    const reason = "工具工作台原子发布全部已放行接口";
    setBusyAction("publish-all");
    setError("");
    setMessage("");
    try {
      const result = await api<BulkPublishResult>(`/api/admin/tools/${toolId}/endpoints/bulk-publish`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, reason, max_risk_level: "medium" }),
      });
      const nextMessage = `已一次性发布 ${result.published_count} 个放行接口。`;
      setMessage(nextMessage);
      notifyToast({ type: "success", message: nextMessage });
      await Promise.all([loadTool(), loadCatalog()]);
    } catch (cause) {
      const nextError = errorMessage(cause);
      setError(nextError);
      notifyToast({ type: "error", message: nextError });
    } finally {
      setBusyAction("");
    }
  }

  async function saveTaskAdapter() {
    if (busyAction) return;
    if (taskAdapterReason.trim().length < 4) { notifyToast({ type: "error", message: "变更原因至少 4 个字符" }); return; }
    let parsed: Record<string, unknown>;
    try { parsed = JSON.parse(taskAdapterText) as Record<string, unknown>; }
    catch { notifyToast({ type: "error", message: "任务适配器不是合法 JSON" }); return; }
    setBusyAction("task-adapter"); setError("");
    try {
      const next = await api<ToolView>(`/api/admin/tools/${toolId}/task-adapter`, { method: "PATCH", body: JSON.stringify({ task_adapter: parsed, reason: taskAdapterReason.trim() }) });
      setTool(next); setTaskAdapterText(JSON.stringify(next.task_adapter || {}, null, 2)); setTaskAdapterReason("");
      notifyToast({ type: "success", message: "任务适配器已更新" });
    } catch (cause) { const nextError = errorMessage(cause); setError(nextError); notifyToast({ type: "error", message: nextError }); }
    finally { setBusyAction(""); }
  }

  function buildV2Adapter() {
    if (![adapterStatusEndpoint, adapterLogsEndpoint, adapterManifestEndpoint, adapterArtifactsEndpoint, adapterBundleEndpoint, adapterDownloadEndpoint, adapterDeleteEndpoint, adapterStorageEndpoint].some(Boolean)) { notifyToast({ type: "error", message: "至少选择一项内部能力接口" }); return; }
    const byId = new Map(adapterEndpoints.map(item => [item.id, item]));
    const routes: Record<string, object> = {};
    const add = (name:string,id:string,config:Record<string,string>={}) => { const endpoint=byId.get(id); if(endpoint) routes[name]={ endpoint_id:endpoint.id, method:endpoint.method, path_template:endpoint.upstream_path, ...config }; };
    add("status",adapterStatusEndpoint); add("logs",adapterLogsEndpoint); add("manifest",adapterManifestEndpoint); add("artifacts",adapterArtifactsEndpoint,{items_selector:"$.artifacts"}); add("bundle_download",adapterBundleEndpoint); add("file_download",adapterDownloadEndpoint); add("file_delete",adapterDeleteEndpoint); add("storage_watermark",adapterStorageEndpoint);
    const value={version:2,id_param:"job_id",file_id_param:"file_id",task_id_selector:"$.job_id",status_selector:"$.status",terminal_statuses:["succeeded","failed","cancelled"],routes,artifacts:{items_selector:"$.artifacts",id_selector:"$.file_id",name_selector:"$.name",role_selector:"$.role",visibility_selector:"$.visibility",status_selector:"$.status",size_selector:"$.size_bytes",content_type_selector:"$.content_type",sha256_selector:"$.sha256",created_at_selector:"$.created_at",expires_at_selector:"$.expires_at"}};
    setTaskAdapterText(JSON.stringify(value,null,2));
    notifyToast({ type:"info", message:"已生成 v2 配置，请核对 JSONPath 后填写原因并保存" });
  }

  async function publish(endpoint: EndpointView, isEnabled: boolean) {
    if (busyEndpointId) return;
    const action = isEnabled ? "发布" : "停用";
    const confirmed = window.confirm(`${action}接口“${endpoint.summary || endpoint.operation_id || endpoint.upstream_path}”？${isEnabled ? "发布后普通用户可能立即调用。" : "停用后依赖该路由的调用会立即失败。"}`);
    if (!confirmed) return;
    setBusyEndpointId(endpoint.id);
    setError("");
    setMessage("");
    try {
      await api(`/api/admin/endpoints/${endpoint.id}/publish`, {
        method: "PATCH",
        body: JSON.stringify({
          is_enabled: isEnabled,
          confirm: isEnabled,
          reason: isEnabled ? "管理员直接发布单接口路由" : "管理员直接停用单接口路由",
        }),
      });
      const nextMessage = isEnabled ? "路由已发布。" : "路由已停用。";
      setMessage(nextMessage);
      notifyToast({ type: "success", message: nextMessage });
      await Promise.all([loadTool(), loadCatalog()]);
    } catch (cause) {
      const nextError = errorMessage(cause);
      setError(nextError);
      notifyToast({ type: "error", message: nextError });
    } finally {
      setBusyEndpointId("");
    }
  }

  function resetFilters() {
    setSearchInput("");
    setStatusFilter("");
    setMethodFilter("");
    setRiskFilter("");
    setGroupFilter("");
    setPage(1);
  }

  if (!tool) {
    return (
      <PortalShell admin title="工具详情工作台">
        {error ? (
          <div className="state-box error" role="alert">
            <p>{error}</p>
            <button className="button secondary compact" type="button" onClick={() => void refreshWorkbench()}>重试</button>
          </div>
        ) : <div className="state-box">正在加载工具详情...</div>}
      </PortalShell>
    );
  }

  const hasFilters = Boolean(deferredSearch || statusFilter || methodFilter || riskFilter || groupFilter);
  const healthStatus = tool.health?.status || "unknown";
  const healthLabel = healthStatus === "unknown" ? "未检测" : tool.health?.reachable ? "可达" : "不可用";
  const healthTone = healthStatus === "unknown" ? "info" : tool.health?.reachable ? "good" : "warn";
  const latestBatchPendingCount = Number(tool.onboarding?.latest_batch_pending_count || 0);
  const releaseReady = Boolean(tool.onboarding?.release_ready);

  return (
    <PortalShell admin title={`${tool.name} 工作台`}>
      <p className="page-intro">工具详情聚合环境、认证、最近导入、接口治理和发布状态。接口目录使用服务端筛选与分页，不会一次加载全部接口。</p>

      <div className="toolbar">
        <div className="toolbar-actions">
          <Link href={`/admin/tools/${tool.id}/settings`} className="button secondary"><Settings2 size={16} />运行配置</Link>
          <Link href={`/admin/tools/${tool.id}/import`} className="button"><FileSearch size={16} />导入新接口</Link>
          <Link href={`/admin/tools/${tool.id}/diff`} className="button secondary"><GitBranch size={16} />同步 OpenAPI</Link>
          <button className="button secondary" onClick={() => void healthTest()} disabled={Boolean(busyAction)}>
            <RefreshCw size={16} className={busyAction === "health" ? "spin" : undefined} />
            {busyAction === "health" ? "测试中" : "连接测试"}
          </button>
        </div>
        <button className="button secondary" onClick={() => void bulkPublishSafeEndpoints()} disabled={Boolean(busyAction) || !releaseReady} title={!releaseReady ? `最近批次仍有 ${latestBatchPendingCount} 个接口待决策或尚未确认` : undefined}>
          <Route size={16} />{busyAction === "publish-all" ? "发布中" : "一键发布全部放行接口"}
        </button>
        <Link href="/admin/tools" className="icon-text-button">返回工具列表</Link>
      </div>
      {(message || error) && <p className={`form-message ${message ? "success" : ""}`}>{message || error}</p>}
      {tool.inconsistent_route_count > 0 && (
        <div className="notice danger" role="alert">
          检测到 {tool.inconsistent_route_count} 条治理状态与线上路由不一致的接口。数据面已拒绝调用，请在下方筛选并停用遗留路由。
        </div>
      )}
      {!releaseReady && tool.onboarding?.latest_batch_id && (
        <div className="notice" role="status">
          最近同步批次尚未满足发布条件：待决策 {latestBatchPendingCount} 个。请先进入“同步 OpenAPI”完成治理和批次确认。
        </div>
      )}

      <div className="metrics">
        <Metric label="已发布" value={String(tool.published_count)} caption="可被 Gateway 调用" tone="green" />
        <Metric label="草稿/候选" value={String(tool.draft_count)} caption="需要治理后发布" />
        <Metric label="默认排除" value={String(tool.excluded_count)} caption="internal/admin/deprecated" tone="amber" />
        <Metric label="阻断项" value={String(tool.blocked_count)} caption="路径冲突、删除或高危阻断" tone={tool.blocked_count ? "red" : "purple"} />
      </div>

      <div className="admin-layout endpoint-workbench-layout" style={{ marginTop: 24 }}>
        <section className="panel endpoint-catalog-panel">
          <div className="toolbar endpoint-catalog-heading">
            <div>
              <h2>路由映射与接口治理</h2>
              <span className="muted">共 {catalog.total} 个匹配接口，第 {catalog.page} / {catalog.page_count} 页</span>
            </div>
            <div className="toolbar-actions">
              <button className="icon-button" type="button" title="刷新接口目录" aria-label="刷新接口目录" onClick={() => void refreshWorkbench(true)} disabled={catalogLoading || Boolean(busyAction)}>
                <RefreshCw size={15} className={catalogLoading ? "spin" : undefined} />
              </button>
              <Link href={`/admin/tools/${tool.id}/import`} className="icon-text-button">进入导入预览<ArrowRight size={16} /></Link>
            </div>
          </div>

          <div className="endpoint-catalog-filters" aria-label="接口筛选">
            <label className="endpoint-catalog-search">
              <Search size={15} />
              <input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索名称、operation_id 或路径" aria-label="搜索接口" />
              {searchInput && <button type="button" onClick={() => setSearchInput("")} aria-label="清空接口搜索"><X size={14} /></button>}
            </label>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="按治理状态筛选">
              <option value="">全部状态</option>
              <option value="published">已发布</option>
              <option value="candidate">候选</option>
              <option value="draft">草稿</option>
              <option value="excluded">已排除</option>
              <option value="blocked">已阻断</option>
            </select>
            <select value={methodFilter} onChange={(event) => setMethodFilter(event.target.value)} aria-label="按请求方法筛选">
              <option value="">全部方法</option>
              {catalog.methods.map((item) => <option value={item} key={item}>{item}</option>)}
            </select>
            <select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)} aria-label="按风险级别筛选">
              <option value="">全部风险</option>
              {['info', 'low', 'medium', 'high', 'blocker'].map((item) => <option value={item} key={item}>{riskLabel(item)}</option>)}
            </select>
            <select value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)} aria-label="按接口分组筛选">
              <option value="">全部分组</option>
              {catalog.groups.map((item) => <option value={item} key={item}>{item}</option>)}
            </select>
            {hasFilters && <button className="button secondary compact" type="button" onClick={resetFilters}>重置</button>}
          </div>

          <div className="table-wrap endpoint-catalog-table-wrap" aria-busy={catalogLoading}>
            <table className="data-table endpoint-catalog-table">
              <thead>
                <tr><th>方法</th><th>接口</th><th>分组</th><th>治理状态</th><th>访问策略</th><th>操作</th></tr>
              </thead>
              <tbody>
                {catalog.items.map((endpoint) => (
                  <tr key={endpoint.id}>
                    <td><span className={endpoint.method === "DELETE" ? "status warn" : "status info"}>{endpoint.method}</span></td>
                    <td className="endpoint-catalog-identity">
                      <strong>{endpoint.summary || endpoint.operation_id || "未命名接口"}</strong>
                      <code>/gateway/{tool.slug}{endpoint.gateway_path || endpoint.upstream_path}</code>
                      <small>{endpoint.operation_id || endpoint.upstream_path}</small>
                    </td>
                    <td>{endpoint.group_name || "未分组"}</td>
                    <td>
                      <span className={endpoint.status === "blocked" ? "status danger" : endpoint.status === "published" ? "status good" : "status warn"}>{statusLabel(endpoint.status)}</span>
                      <small className="cell-note">风险 {riskLabel(endpoint.risk_level)}</small>
                    </td>
                    <td>
                      <span className={endpoint.access_policy.complete && endpoint.access_policy.confirmed ? "status good" : "status warn"}>{accessModeLabel(endpoint.access_policy.access_mode)}</span>
                      <small className="cell-note">{endpoint.access_policy.confirmed ? "已确认" : endpoint.access_policy.reason || "待确认"}</small>
                      {endpoint.route_policy && <><small className="cell-note">v{endpoint.route_policy.route_version} · {endpoint.route_policy.request_timeout_seconds}s · {formatBytes(endpoint.route_policy.max_request_bytes)}</small><small className="cell-note">路由 ID：<code>{endpoint.route_policy.id}</code></small></>}
                    </td>
                    <td className="endpoint-catalog-actions">
                      <Link href={`/admin/tools/${tool.id}/endpoints/${endpoint.id}`} className="button secondary compact">配置</Link>
                      <button
                        className={endpoint.enabled ? "button secondary compact" : "button compact"}
                        disabled={busyEndpointId === endpoint.id || (!endpoint.enabled && (endpoint.status === "excluded" || endpoint.status === "blocked" || endpoint.risk_level === "high" || endpoint.risk_level === "blocker"))}
                        title={!endpoint.enabled && endpoint.risk_level === "high" ? "高风险接口需进入配置页填写审计原因后发布" : undefined}
                        onClick={() => void publish(endpoint, !endpoint.enabled)}
                      >
                        {busyEndpointId === endpoint.id ? "处理中" : endpoint.enabled ? "停用" : "发布"}
                      </button>
                    </td>
                  </tr>
                ))}
                {!catalogLoading && !catalog.items.length && (
                  <tr><td colSpan={6}><div className="state-box">{hasFilters ? "没有匹配当前筛选条件的接口。" : "尚未导入接口，请先进入导入页面。"}</div></td></tr>
                )}
                {catalogLoading && !catalog.items.length && <tr><td colSpan={6}><div className="state-box">正在加载接口目录...</div></td></tr>}
              </tbody>
            </table>
          </div>

          <div className="endpoint-catalog-pagination">
            <label>每页
              <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))} disabled={catalogLoading}>
                <option value={25}>25</option><option value={50}>50</option><option value={100}>100</option>
              </select>
            </label>
            <span>第 {catalog.page} / {catalog.page_count} 页，共 {catalog.total} 个</span>
            <div>
              <button className="icon-button" type="button" aria-label="上一页接口" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={catalogLoading || page <= 1}><ChevronLeft size={16} /></button>
              <button className="icon-button" type="button" aria-label="下一页接口" onClick={() => setPage((current) => Math.min(catalog.page_count, current + 1))} disabled={catalogLoading || page >= catalog.page_count}><ChevronRight size={16} /></button>
            </div>
          </div>
        </section>

        <aside className="panel tool-runtime-panel">
          <Activity size={25} color="#1a5ec0" />
          <h2 style={{ marginTop: 12 }}>工具运行态</h2>
          <div className="list-row"><span>工具状态</span><span className={tool.is_enabled ? "status good" : "status warn"}>{statusLabel(tool.status)}</span></div>
          <div className="list-row"><span>上游健康</span><span className={`status ${healthTone}`}>{healthLabel}</span></div>
          <div className="list-row"><span>部署服务器</span><strong>{tool.base_url_masked}</strong></div>
          <div className="list-row"><span>限流策略</span><strong>{tool.rate_limit_per_minute}/分钟</strong></div>
          <div className="signal-card info"><strong><Route size={15} style={{ verticalAlign: "middle", marginRight: 6 }} />路由前缀</strong><p className="endpoint-path">/gateway/{tool.slug}</p></div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong><KeyRound size={15} style={{ verticalAlign: "middle", marginRight: 6 }} />服务端认证</strong>
            <p className="muted">类型：{tool.auth_type}<br />Token：{tool.token_configured ? "已配置（不回显）" : "未配置"}<br />Header：{tool.token_label || "默认"}</p>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong><ShieldCheck size={15} style={{ verticalAlign: "middle", marginRight: 6 }} />最近导入</strong>
            <p className="muted">{tool.latest_openapi_imported_at ? new Date(tool.latest_openapi_imported_at).toLocaleString() : "尚未导入"}<br />SHA-256：{tool.latest_openapi_sha256 ? `${tool.latest_openapi_sha256.slice(0, 16)}...` : "无"}</p>
          </div>
        </aside>
      </div>
      <details className="panel task-adapter-panel" style={{ marginTop: 24 }} open={Object.keys(tool.task_adapter || {}).length > 0}>
        <summary className="task-adapter-summary">
          <span><strong>任务与文件能力适配器</strong><small>仅在工具需要任务状态、Manifest 或文件同步能力时配置</small></span>
          <span className={Object.keys(tool.task_adapter || {}).length ? "status good" : "status info"}>{Object.keys(tool.task_adapter || {}).length ? `v${String(tool.task_adapter.version || 1)}` : "未配置"}</span>
        </summary>
        <div className="task-adapter-content">
          <p className="muted">v2 将任务状态、Manifest、逐文件下载和删除映射到同一工具已导入的接口。旧 v1 配置继续只读兼容。</p>
          <div className="adapter-guided-grid"><label className="form-field">状态接口<select value={adapterStatusEndpoint} onChange={event=>setAdapterStatusEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>["GET","HEAD"].includes(item.method)).map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><label className="form-field">日志接口<select value={adapterLogsEndpoint} onChange={event=>setAdapterLogsEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>item.method==="GET").map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><label className="form-field">Manifest 接口<select value={adapterManifestEndpoint} onChange={event=>setAdapterManifestEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>item.method==="GET").map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><label className="form-field">制品列表接口<select value={adapterArtifactsEndpoint} onChange={event=>setAdapterArtifactsEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>item.method==="GET").map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><label className="form-field">整包下载接口<select value={adapterBundleEndpoint} onChange={event=>setAdapterBundleEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>["GET","HEAD"].includes(item.method)).map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><label className="form-field">单文件下载接口<select value={adapterDownloadEndpoint} onChange={event=>setAdapterDownloadEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>["GET","HEAD"].includes(item.method)).map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><label className="form-field">单文件删除接口<select value={adapterDeleteEndpoint} onChange={event=>setAdapterDeleteEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>["DELETE","POST"].includes(item.method)).map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><label className="form-field">存储水位接口<select value={adapterStorageEndpoint} onChange={event=>setAdapterStorageEndpoint(event.target.value)}><option value="">不配置</option>{adapterEndpoints.filter(item=>["GET","HEAD"].includes(item.method)).map(item=><option value={item.id} key={item.id}>{item.method} {item.upstream_path}</option>)}</select></label><button className="button secondary" type="button" onClick={buildV2Adapter} disabled={!adapterEndpoints.length}>生成 v2 配置</button></div>
          <div className="editor-layout"><label className="form-field">高级 JSON 视图<textarea className="code-editor" rows={18} value={taskAdapterText} onChange={(event) => setTaskAdapterText(event.target.value)} spellCheck={false} /><small>Manifest 默认上限 1 MiB / 1000 项；文件 ID 必须稳定且不透明，不能返回绝对路径或任意 URL。</small></label><div><div className="signal-card neutral"><strong>保存边界</strong><p className="muted">服务端会校验接口归属、方法、路径模板和 JSONPath。保存后只影响后续同步；历史文件仍绑定创建时的存储端点版本。</p></div><label className="form-field">变更原因<input value={taskAdapterReason} onChange={(event) => setTaskAdapterReason(event.target.value)} maxLength={500} placeholder="说明本次能力映射变更" /></label><button className="button" disabled={Boolean(busyAction)} onClick={() => void saveTaskAdapter()}>{busyAction === "task-adapter" ? "保存中" : "验证并保存"}</button></div></div>
        </div>
      </details>
    </PortalShell>
  );
}
