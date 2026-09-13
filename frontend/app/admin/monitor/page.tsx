"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, Copy, Eye, HardDrive, MailWarning, RefreshCw, Search, ShieldCheck, Timer, UsersRound, Wrench } from "lucide-react";
import { CallDetailDrawer } from "@/components/call-detail-drawer";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { formatBytes, GatewayCall } from "@/lib/calls";
import { copyToClipboard } from "@/lib/clipboard";
import { notifyToast } from "@/lib/toast";

type MonitorSummary = {
  total_requests: number;
  success_requests: number;
  failed_requests: number;
  success_rate: number;
  avg_duration_ms: number;
  request_bytes: number;
  response_bytes: number;
  recent_failures: GatewayRequestView[];
};

type MonitorMetrics = {
  window: { start: string; end: string; minutes: number };
  requests: {
    total: number;
    success: number;
    failed: number;
    success_rate: number;
    error_rate: number;
    rate_limited: number;
  };
  latency_ms: { avg: number; p50: number; p95: number; p99: number };
  traffic_bytes: { request: number; response: number; total: number };
  key_anomalies: { count: number };
  alerts: { count: number; critical_count: number };
};

type MonitorAlert = {
  id: string;
  type: string;
  severity: "critical" | "warning" | "info";
  title: string;
  message: string;
  request_id?: string | null;
  tool_slug?: string | null;
  api_key_prefix?: string | null;
  username?: string | null;
  status_code?: number | null;
  count?: number | null;
  failure_rate?: number | null;
  last_seen_at?: string | null;
};

type GatewayRequestView = GatewayCall & { id: string };

type RankingItem = {
  user_id?: string | null;
  tool_slug?: string;
  username?: string;
  total_requests: number;
  avg_duration_ms: number;
  failed_requests: number;
  last_called_at: string | null;
};

type EmailOutboxMonitor = {
  smtp_configured: boolean;
  counts: Record<string, number>;
  items: { id: string; recipient: string; subject: string; status: string; attempts: number; last_error: string; next_attempt_at: string; updated_at: string }[];
};

type FileOperationalMetrics = {
  total_files: number;
  ready_files: number;
  logical_bytes: number;
  sync_backlog: number;
  sync_failures: number;
  active_downloads: number;
  worker_required: boolean;
  worker_status: "healthy" | "offline" | "not_required";
  active_workers: number;
  workers: Array<{ instance_id: string; process_id: number; started_at: string; last_seen_at: string; processed_jobs: number; last_error: string }>;
  storage_watermarks: Array<{ tool_id: string; tool_slug: string; tool_name: string; total_bytes: number | null; free_bytes: number | null; free_ratio: number | null; checked_at: string | null; level: "ok" | "warning" | "critical" | "stale" | "unknown" }>;
};

type FilterOverrides = {
  username?: string;
  toolSlug?: string;
  statusCode?: string;
  requestId?: string;
  start?: string;
  end?: string;
};

function formatRate(value: number) {
  return `${(((value ?? 0) * 100) || 0).toFixed(1)}%`;
}

function statusTone(status: number) {
  if (status < 400) return "status good";
  if (status < 500) return "status warn";
  return "status info";
}

function severityTone(severity: MonitorAlert["severity"]) {
  if (severity === "critical") return "status warn";
  if (severity === "warning") return "status info";
  return "status good";
}

export default function AdminMonitorPage() {
  const [summary, setSummary] = useState<MonitorSummary | null>(null);
  const [metrics, setMetrics] = useState<MonitorMetrics | null>(null);
  const [alerts, setAlerts] = useState<MonitorAlert[]>([]);
  const [requests, setRequests] = useState<GatewayRequestView[]>([]);
  const [tools, setTools] = useState<RankingItem[]>([]);
  const [users, setUsers] = useState<RankingItem[]>([]);
  const [emailOutbox, setEmailOutbox] = useState<EmailOutboxMonitor | null>(null);
  const [fileMetrics, setFileMetrics] = useState<FileOperationalMetrics | null>(null);
  const [fileMetricsError, setFileMetricsError] = useState("");
  const [username, setUsername] = useState("");
  const [toolSlug, setToolSlug] = useState("");
  const [statusCode, setStatusCode] = useState("");
  const [requestId, setRequestId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshSeconds, setRefreshSeconds] = useState(30);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [selectedCall, setSelectedCall] = useState<GatewayCall | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState("");
  const requestSequence = useRef(0);
  const loadingRef = useRef(false);
  const appliedFiltersRef = useRef<FilterOverrides>({});

  const hasSummarySamples = Boolean(summary?.total_requests);
  const hasMetricSamples = Boolean(metrics?.requests.total);
  const successRate = useMemo(() => summary ? hasSummarySamples ? formatRate(summary.success_rate) : "--" : "-", [hasSummarySamples, summary]);
  const errorRate = useMemo(() => metrics ? hasMetricSamples ? formatRate(metrics.requests.error_rate) : "--" : "-", [hasMetricSamples, metrics]);
  const loadFileMetrics = useCallback(async () => {
    setFileMetricsError("");
    try {
      setFileMetrics(await api<FileOperationalMetrics>("/api/admin/files/metrics"));
    } catch (cause) {
      setFileMetricsError(errorMessage(cause));
    }
  }, []);

  function buildParams(filters: FilterOverrides) {
    const params = new URLSearchParams();
    const nextUsername = filters.username ?? "";
    const nextToolSlug = filters.toolSlug ?? "";
    const nextStatusCode = filters.statusCode ?? "";
    const nextRequestId = filters.requestId ?? "";
    const nextStart = filters.start ?? "";
    const nextEnd = filters.end ?? "";
    if (nextUsername.trim()) params.set("username", nextUsername.trim());
    if (nextToolSlug.trim()) params.set("tool_slug", nextToolSlug.trim());
    if (nextStatusCode.trim()) params.set("status_code", nextStatusCode.trim());
    if (nextRequestId.trim()) params.set("request_id", nextRequestId.trim());
    if (nextStart) params.set("start", nextStart);
    if (nextEnd) params.set("end", nextEnd);
    return params.toString();
  }

  const load = useCallback(async (filters: FilterOverrides, showToast = false) => {
    if (loadingRef.current && !showToast) return;
    void loadFileMetrics();
    const sequence = ++requestSequence.current;
    loadingRef.current = true;
    setLoading(true);
    setError("");
    const query = buildParams(filters);
    try {
      const [nextSummary, nextMetrics, nextAlerts, nextRequests, nextTools, nextUsers, nextEmailOutbox] = await Promise.all([
        api<MonitorSummary>(`/api/admin/monitor/summary${query ? `?${query}` : ""}`),
        api<MonitorMetrics>(`/api/admin/monitor/metrics${query ? `?${query}` : ""}`),
        api<MonitorAlert[]>(`/api/admin/monitor/alerts${query ? `?${query}` : ""}`),
        api<GatewayRequestView[]>(`/api/admin/monitor/requests${query ? `?${query}` : ""}`),
        api<RankingItem[]>("/api/admin/monitor/tools"),
        api<RankingItem[]>("/api/admin/monitor/users"),
        api<EmailOutboxMonitor>("/api/admin/monitor/email-outbox"),
      ]);
      if (sequence !== requestSequence.current) return;
      setSummary(nextSummary);
      setMetrics(nextMetrics);
      setAlerts(nextAlerts);
      setRequests(nextRequests);
      setTools(nextTools);
      setUsers(nextUsers);
      setEmailOutbox(nextEmailOutbox);
      setLastUpdatedAt(new Date());
      if (showToast) notifyToast({ type: "success", message: `监控查询完成：${nextRequests.length} 条调用记录` });
    } catch (cause) {
      if (sequence !== requestSequence.current) return;
      const message = errorMessage(cause);
      setError(message);
      if (showToast) notifyToast({ type: "error", message });
    } finally {
      if (sequence === requestSequence.current) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  }, [loadFileMetrics]);

  function currentFilters(): FilterOverrides {
    return { username, toolSlug, statusCode, requestId, start, end };
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (start && end && new Date(start) > new Date(end)) {
      const message = "开始时间不能晚于结束时间";
      setError(message);
      notifyToast({ type: "error", message });
      return;
    }
    const filters = currentFilters();
    appliedFiltersRef.current = filters;
    if (start || end) setAutoRefresh(false);
    void load(filters, true);
  }

  function searchByRequestId(nextRequestId: string) {
    setRequestId(nextRequestId);
    const filters = { ...currentFilters(), requestId: nextRequestId };
    appliedFiltersRef.current = filters;
    void load(filters, true);
  }

  async function copyRequestId(nextRequestId: string) {
    await copyToClipboard(nextRequestId, "request_id 已复制");
  }

  async function openCall(nextRequestId: string) {
    setDetailLoadingId(nextRequestId);
    try {
      setSelectedCall(await api<GatewayCall>(`/api/admin/calls/${nextRequestId}`));
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setDetailLoadingId("");
    }
  }

  useEffect(() => {
    void load({}, false);
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = window.setInterval(() => {
      if (document.hidden || loadingRef.current) return;
      void load(appliedFiltersRef.current, false);
    }, refreshSeconds * 1000);
    return () => window.clearInterval(interval);
  }, [autoRefresh, load, refreshSeconds]);

  const workerText = !fileMetrics ? "-" : fileMetrics.worker_status === "healthy" ? `${fileMetrics.active_workers} 个进程在线` : fileMetrics.worker_status === "offline" ? "同步进程离线" : "当前无需运行";
  const storageAlerts = fileMetrics?.storage_watermarks.filter((item) => item.level === "critical" || item.level === "warning" || item.level === "stale") ?? [];
  const unknownStorageCount = fileMetrics?.storage_watermarks.filter((item) => item.level === "unknown").length ?? 0;

  return (
    <PortalShell admin title="调用监控与告警">
      <p className="page-intro">
        运行时性能监控与异常诊断。查看用户程序通过统一 Gateway 调用各科研工具的实时性能指标、告警信息和调用记录，快速定位问题和追踪异常。
      </p>

      <div className="monitor-live-bar">
        <div className="monitor-live-state">
          <span className={autoRefresh ? "live-dot active" : "live-dot"} aria-hidden="true" />
          <span><strong>{autoRefresh ? "自动刷新中" : "自动刷新已暂停"}</strong><small>{lastUpdatedAt ? `最后更新 ${lastUpdatedAt.toLocaleTimeString()}` : "等待首次数据"}</small></span>
        </div>
        <div className="monitor-live-controls">
          <label className="toggle-control">
            <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
            <span>自动刷新</span>
          </label>
          <label>
            刷新周期
            <select value={refreshSeconds} onChange={(event) => setRefreshSeconds(Number(event.target.value))} disabled={!autoRefresh}>
              <option value={15}>15 秒</option>
              <option value={30}>30 秒</option>
              <option value={60}>60 秒</option>
            </select>
          </label>
          <button className="icon-text-button" type="button" onClick={() => void load(appliedFiltersRef.current, true)} disabled={loading}>
            <RefreshCw size={15} className={loading ? "spin" : undefined} /><span>{loading ? "刷新中" : "立即刷新"}</span>
          </button>
        </div>
      </div>

      <div className="metrics">
        <Metric label="总调用量" value={summary ? String(summary.total_requests) : "-"} caption="当前过滤条件内" />
        <Metric label="成功率" value={successRate} caption={hasSummarySamples ? `${summary?.success_requests ?? 0} 成功 / ${summary?.failed_requests ?? 0} 失败` : "当前没有调用样本"} tone="green" />
        <Metric label="错误率" value={errorRate} caption={hasMetricSamples ? `限流 ${metrics?.requests.rate_limited ?? 0} 次` : "最近窗口没有调用样本"} tone={metrics?.requests.error_rate ? "red" : "blue"} />
        <Metric label="p95 / p99" value={metrics ? hasMetricSamples ? `${metrics.latency_ms.p95} / ${metrics.latency_ms.p99} ms` : "--" : "-"} caption={hasMetricSamples ? `p50 ${metrics?.latency_ms.p50 ?? 0} ms，均值 ${metrics?.latency_ms.avg ?? 0} ms` : "最近窗口没有延迟样本"} tone="purple" />
      </div>
      <div className="metrics" style={{ marginTop: 20 }}>
        <Metric label="流量" value={summary ? hasSummarySamples ? formatBytes(summary.request_bytes + summary.response_bytes) : "--" : "-"} caption="请求 + 响应字节" tone="amber" />
        <Metric label="Key 异常" value={metrics ? String(metrics.key_anomalies.count) : "-"} caption="近期失败率或限流异常" tone={metrics?.key_anomalies.count ? "red" : "blue"} />
        <Metric label="实时告警" value={metrics ? String(metrics.alerts.count) : "-"} caption={`${metrics?.alerts.critical_count ?? 0} 条严重告警`} tone={metrics?.alerts.critical_count ? "red" : "amber"} />
      </div>

      <section className="panel file-operations-monitor">
        <div className="toolbar">
          <div><h2><HardDrive size={18} /> 文件与制品运行状态</h2><p className="muted">同步进程、逻辑容量和工具服务器存储风险在此集中监控。</p></div>
          <Link className="button secondary compact" href="/admin/files">查看任务与文件</Link>
        </div>
        <div className="metrics file-monitor-metrics">
          <Metric label="逻辑容量" value={fileMetrics ? formatBytes(fileMetrics.logical_bytes) : "-"} caption={`${fileMetrics?.total_files ?? 0} 个登记文件`} />
          <Metric label="同步积压" value={fileMetrics ? String(fileMetrics.sync_backlog) : "-"} caption={`失败重试 ${fileMetrics?.sync_failures ?? 0}`} tone={fileMetrics?.sync_failures ? "red" : "blue"} />
          <Metric label="可下载文件" value={fileMetrics ? String(fileMetrics.ready_files) : "-"} caption={`活跃下载 ${fileMetrics?.active_downloads ?? 0}`} tone="green" />
          <Metric label="同步进程" value={workerText} caption={fileMetrics?.workers?.[0]?.last_seen_at ? `最近心跳 ${new Date(fileMetrics.workers[0].last_seen_at).toLocaleTimeString()}` : "每 30 秒判定一次"} tone={fileMetrics?.worker_status === "offline" ? "red" : "blue"} />
        </div>
        {fileMetricsError && <div className="state-box error"><p>文件运行指标加载失败：{fileMetricsError}</p><button className="button secondary compact" type="button" onClick={() => void loadFileMetrics()}>重试</button></div>}
        {fileMetrics?.worker_status === "offline" && <div className="notice danger"><strong>文件同步进程未运行</strong><p>已配置制品能力，但最近 30 秒没有进程心跳。长任务文件不会继续后台同步。</p></div>}
        {(storageAlerts.length > 0 || unknownStorageCount > 0) && <div className="file-storage-alerts">
          {storageAlerts.map((item) => <div className="list-row" key={item.tool_id}><span><strong>{item.tool_name}</strong><small>{item.checked_at ? `检查于 ${new Date(item.checked_at).toLocaleString()}` : "尚无检查时间"}</small></span><span className={item.level === "critical" ? "status danger" : "status warn"}>{item.level === "stale" ? "水位数据已过期" : `剩余 ${((item.free_ratio ?? 0) * 100).toFixed(1)}%`}</span></div>)}
          {unknownStorageCount > 0 && <div className="list-row"><span><strong>{unknownStorageCount} 个工具未上报存储水位</strong><small>旧工具需确认存储风险；新工具应配置 v2 水位能力。</small></span><Link className="button secondary compact" href="/admin/tools">检查工具</Link></div>}
        </div>}
      </section>

      <section className="panel" style={{ marginTop: 24 }}>
        <form className="toolbar" onSubmit={submit}>
          <div className="toolbar-actions" style={{ flexWrap: "wrap" }}>
            <label className="form-field" style={{ margin: 0, minWidth: 180 }}>
              用户名
              <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="例如 alice_lab" />
            </label>
            <label className="form-field" style={{ margin: 0, minWidth: 180 }}>
              工具 slug
              <input value={toolSlug} onChange={(event) => setToolSlug(event.target.value)} placeholder="例如 tomodd" />
            </label>
            <label className="form-field" style={{ margin: 0, minWidth: 150 }}>
              状态码
              <input value={statusCode} onChange={(event) => setStatusCode(event.target.value)} placeholder="例如 502" inputMode="numeric" />
            </label>
            <label className="form-field" style={{ margin: 0, minWidth: 280 }}>
              request_id
              <input value={requestId} onChange={(event) => setRequestId(event.target.value)} placeholder="精确定位一次调用" />
            </label>
            <label className="form-field" style={{ margin: 0, minWidth: 200 }}>
              开始时间
              <input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} />
            </label>
            <label className="form-field" style={{ margin: 0, minWidth: 200 }}>
              结束时间
              <input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} />
            </label>
          </div>
          <button className="button secondary compact" type="submit" disabled={loading}>
            <RefreshCw size={14} />
            {loading ? "查询中" : "查询"}
          </button>
        </form>
        {error && <p className="form-message">{error}</p>}
      </section>

      <div className="dashboard-grid">
        <section className="panel">
          <h2>
            <AlertTriangle size={18} /> 实时告警
          </h2>
          {alerts.map((item) => (
            <div className="list-row" key={item.id} style={{ alignItems: "flex-start", gap: 12 }}>
              <span>
                <span className={severityTone(item.severity)}>{item.severity === "critical" ? "严重" : item.severity === "warning" ? "预警" : "提示"}</span>
                <strong style={{ display: "block", marginTop: 8 }}>{item.title}</strong>
                <small className="muted">{item.message}</small>
                <br />
                <small className="muted">
                  {item.tool_slug ? `工具 ${item.tool_slug}` : ""}
                  {item.api_key_prefix ? ` · Key ${item.api_key_prefix}` : ""}
                  {item.last_seen_at ? ` · ${new Date(item.last_seen_at).toLocaleString()}` : ""}
                </small>
              </span>
              {item.request_id && (
                <button className="button secondary compact" type="button" onClick={() => searchByRequestId(item.request_id || "")}>
                  <Search size={13} /> 定位
                </button>
              )}
            </div>
          ))}
          {lastUpdatedAt && !alerts.length && <div className="state-box">最近 60 分钟未发现 Key、工具或流量异常。</div>}
        </section>

        <aside className="panel">
          <h2>
            <ShieldCheck size={18} /> 最近失败
          </h2>
          {(summary?.recent_failures ?? []).map((item) => (
            <div className="list-row" key={item.id}>
              <span>
                {item.tool_slug} · {item.status_code}
                <br />
                <small className="muted">{item.error_code || item.path}</small>
              </span>
              <button className="button secondary compact" type="button" onClick={() => searchByRequestId(item.request_id)}>
                <Search size={13} /> {item.request_id.slice(0, 8)}
              </button>
            </div>
          ))}
          {summary && !summary.recent_failures.length && <div className="state-box">当前过滤条件下暂无失败请求。</div>}
        </aside>
      </div>

      <div className="dashboard-grid">
        <section className="panel">
          <h2>
            <Activity size={18} /> 最近调用
          </h2>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>用户 / Key</th>
                  <th>接口</th>
                  <th>状态</th>
                  <th>耗时</th>
                  <th>request_id / 操作</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((item) => (
                  <tr key={item.id}>
                    <td>{new Date(item.created_at).toLocaleString()}</td>
                    <td>
                      {item.username || "未知用户"}
                      <br />
                      <span className="muted">{item.api_key_prefix || "无 Key 前缀"}</span>
                    </td>
                    <td>
                      <span className={item.method === "DELETE" ? "status warn" : "status info"}>{item.method}</span>
                      <div className="endpoint-path">
                        /gateway/{item.tool_slug}
                        {item.path}
                      </div>
                      <span className="muted">{item.operation_id || item.upstream_path || "未绑定 operation_id"}</span>
                    </td>
                    <td>
                      <span className={statusTone(item.status_code)}>{item.status_code}</span>
                      <br />
                      <span className="muted">{item.error_code || `上游 ${item.upstream_status_code ?? "-"}`}</span>
                    </td>
                    <td>
                      {item.duration_ms} ms
                      <br />
                      <span className="muted">
                        {formatBytes(item.request_bytes)} / {formatBytes(item.response_bytes)}
                      </span>
                    </td>
                    <td>
                      <code className="audit-request-id">{item.request_id}</code>
                      <div className="toolbar-actions" style={{ marginTop: 8 }}>
                        <button className="icon-button" type="button" aria-label={`查看调用 ${item.request_id}`} disabled={detailLoadingId === item.request_id} onClick={() => void openCall(item.request_id)}><Eye size={14} /></button>
                        <button className="icon-button" type="button" aria-label={`复制 request_id ${item.request_id}`} onClick={() => void copyRequestId(item.request_id)}><Copy size={14} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
                {lastUpdatedAt && !requests.length && (
                  <tr>
                    <td colSpan={6}>
                      <div className="state-box">暂无调用记录。用户通过 X-API-Key 调用已发布路由后会出现在这里。</div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <aside className="panel">
          <h2>
            <Timer size={18} /> 指标窗口
          </h2>
          <div className="list-row">
            <span>
              最近窗口
              <br />
              <small className="muted">
                {metrics?.window.start ? new Date(metrics.window.start).toLocaleString() : "-"} 至{" "}
                {metrics?.window.end ? new Date(metrics.window.end).toLocaleString() : "-"}
              </small>
            </span>
            <strong>{metrics?.window.minutes ?? 60} 分钟</strong>
          </div>
          <div className="list-row">
            <span>
              请求体 / 响应体
              <br />
              <small className="muted">用于识别上传或下载异常</small>
            </span>
            <strong>{formatBytes(metrics?.traffic_bytes.total ?? 0)}</strong>
          </div>
        </aside>
      </div>

      <div className="dashboard-grid">
        <section className="panel">
          <h2>
            <Wrench size={18} /> 工具调用排行
          </h2>
          {tools.map((item) => (
            <div className="list-row" key={item.tool_slug || "unknown-tool"}>
              <span>
                {item.tool_slug || "未知工具"}
                <br />
                <small className="muted">
                  失败 {item.failed_requests} · 平均 {item.avg_duration_ms} ms
                </small>
              </span>
              <strong>{item.total_requests}</strong>
            </div>
          ))}
          {lastUpdatedAt && !tools.length && <div className="state-box">暂无工具调用排行。</div>}
        </section>

        <section className="panel">
          <h2>
            <UsersRound size={18} /> 用户调用排行
          </h2>
          {users.map((item) => (
            <div className="list-row" key={item.username || "unknown-user"}>
              <span>
                {item.user_id ? <Link className="text-icon-button" href={`/admin/users/${item.user_id}`}>{item.username || "未知用户"}</Link> : (item.username || "未知用户")}
                <br />
                <small className="muted">
                  失败 {item.failed_requests} · 平均 {item.avg_duration_ms} ms
                </small>
              </span>
              <strong>{item.total_requests}</strong>
            </div>
          ))}
          {lastUpdatedAt && !users.length && <div className="state-box">暂无用户调用排行。</div>}
        </section>
      </div>

      <section className="panel" style={{ marginTop: 24 }}>
        <div className="toolbar"><div><h2><MailWarning size={18} /> 邮件 Outbox</h2><p className="muted">邮件失败不回滚业务事务；发送进程会按退避策略重试。</p></div><span className={emailOutbox?.smtp_configured ? "status good" : "status warn"}>{emailOutbox?.smtp_configured ? "SMTP 已配置" : "SMTP 未配置"}</span></div>
        <div className="metrics email-metrics"><Metric label="待发送" value={String(emailOutbox?.counts.pending ?? "-")} caption="等待发送进程处理" /><Metric label="重试中" value={String(emailOutbox?.counts.retry ?? "-")} caption="尚未达到最大次数" tone="amber" /><Metric label="发送失败" value={String(emailOutbox?.counts.failed ?? "-")} caption="需要管理员检查" tone="red" /><Metric label="已发送" value={String(emailOutbox?.counts.sent ?? "-")} caption="正文已从队列清除" tone="green" /></div>
        {emailOutbox?.items.map((item) => <div className="list-row" key={item.id}><span><strong>{item.subject}</strong><br/><small className="muted">{item.recipient} · 已尝试 {item.attempts} 次 · 下次 {new Date(item.next_attempt_at).toLocaleString()}</small><br/><small className="key-disable-reason">{item.last_error || "未记录错误原因"}</small></span><span className="status warn">{item.status}</span></div>)}
        {emailOutbox && !emailOutbox.items.length && <div className="state-box success-state">当前没有失败或重试中的邮件。</div>}
      </section>

      <p className="notice" style={{ marginTop: 24 }}>
        <Timer size={15} style={{ verticalAlign: "middle", marginRight: 6 }} />
        本页指标来自脱敏后的 Gateway 调用记录。后续接入 Prometheus/OpenTelemetry 时，可复用当前字段模型继续外送指标与链路。
      </p>
      <CallDetailDrawer call={selectedCall} admin onClose={() => setSelectedCall(null)} />
    </PortalShell>
  );
}
