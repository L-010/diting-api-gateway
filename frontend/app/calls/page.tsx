"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Copy, Download, Eye, FilterX, RefreshCw, Search } from "lucide-react";
import { CallDetailDrawer } from "@/components/call-detail-drawer";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { formatBytes, GatewayCall, GatewayCallPage, statusTone } from "@/lib/calls";
import { copyToClipboard } from "@/lib/clipboard";
import { notifyToast } from "@/lib/toast";

type CallFilters = {
  requestId: string;
  toolSlug: string;
  statusCode: string;
  result: string;
  method: string;
  keyId: string;
  start: string;
  end: string;
};

const PAGE_SIZE = 30;
const emptyFilters: CallFilters = { requestId: "", toolSlug: "", statusCode: "", result: "", method: "", keyId: "", start: "", end: "" };
type UserKey = { id: string; label: string; prefix: string; status: string; is_expired: boolean };

function toLocalInput(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export default function CallsPage() {
  const [items, setItems] = useState<GatewayCall[]>([]);
  const [keys, setKeys] = useState<UserKey[]>([]);
  const [filters, setFilters] = useState<CallFilters>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<CallFilters>(emptyFilters);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedCall, setSelectedCall] = useState<GatewayCall | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState("");
  const [refreshInterval, setRefreshInterval] = useState("0");
  const [exporting, setExporting] = useState(false);
  const requestSequence = useRef(0);
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  async function load(nextPage: number, nextFilters: CallFilters, showToast = false, refreshOverride = refreshInterval) {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ page: String(nextPage), page_size: String(PAGE_SIZE) });
    if (nextFilters.requestId.trim()) params.set("request_id", nextFilters.requestId.trim());
    if (nextFilters.toolSlug.trim()) params.set("tool_slug", nextFilters.toolSlug.trim());
    if (nextFilters.statusCode) params.set("status_code", nextFilters.statusCode);
    if (nextFilters.result) params.set("result", nextFilters.result);
    if (nextFilters.method) params.set("method", nextFilters.method);
    if (nextFilters.keyId) params.set("api_key_id", nextFilters.keyId);
    if (nextFilters.start) params.set("start", new Date(nextFilters.start).toISOString());
    if (nextFilters.end) params.set("end", new Date(nextFilters.end).toISOString());
    if (refreshOverride !== "0") params.set("refresh", refreshOverride);
    window.history.replaceState(null, "", `/calls?${params.toString()}`);
    try {
      const response = await api<GatewayCallPage>(`/api/me/calls?${params.toString()}`);
      if (sequence !== requestSequence.current) return;
      setItems(response.items);
      setTotal(response.total);
      setPage(response.page);
      setHasMore(response.has_more);
      setAppliedFilters(nextFilters);
      if (showToast) notifyToast({ type: "success", message: `调用记录查询完成：共 ${response.total} 条` });
    } catch (cause) {
      if (sequence !== requestSequence.current) return;
      const message = errorMessage(cause);
      setError(message);
      if (showToast) notifyToast({ type: "error", message });
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void load(1, filters, true);
  }

  function resetFilters() {
    setFilters(emptyFilters);
    window.history.replaceState(null, "", "/calls");
    void load(1, emptyFilters, true);
  }

  function changePage(nextPage: number) {
    void load(nextPage, appliedFilters);
  }

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const initialFilters = {
      requestId: query.get("request_id") || "",
      toolSlug: query.get("tool_slug") || "",
      statusCode: query.get("status_code") || "",
      result: query.get("result") || "",
      method: query.get("method") || "",
      keyId: query.get("api_key_id") || "",
      start: query.get("start") ? toLocalInput(query.get("start") as string) : "",
      end: query.get("end") ? toLocalInput(query.get("end") as string) : "",
    };
    const initialPage = Math.max(1, Number(query.get("page") || "1") || 1);
    const initialRefresh = ["15", "30", "60"].includes(query.get("refresh") || "") ? String(query.get("refresh")) : "0";
    setRefreshInterval(initialRefresh);
    setFilters(initialFilters);
    void load(initialPage, initialFilters, false, initialRefresh);
    api<UserKey[]>("/api/me/api-keys").then(setKeys).catch(() => setKeys([]));
  }, []);

  useEffect(() => {
    const seconds = Number(refreshInterval);
    if (!seconds) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(page, appliedFilters);
    }, seconds * 1000);
    return () => window.clearInterval(timer);
  }, [appliedFilters, page, refreshInterval]);

  async function exportCsv() {
    if (exporting) return;
    setExporting(true);
    setError("");
    const params = new URLSearchParams();
    if (appliedFilters.requestId.trim()) params.set("request_id", appliedFilters.requestId.trim());
    if (appliedFilters.toolSlug.trim()) params.set("tool_slug", appliedFilters.toolSlug.trim());
    if (appliedFilters.statusCode) params.set("status_code", appliedFilters.statusCode);
    if (appliedFilters.result) params.set("result", appliedFilters.result);
    if (appliedFilters.method) params.set("method", appliedFilters.method);
    if (appliedFilters.keyId) params.set("api_key_id", appliedFilters.keyId);
    if (appliedFilters.start) params.set("start", new Date(appliedFilters.start).toISOString());
    if (appliedFilters.end) params.set("end", new Date(appliedFilters.end).toISOString());
    try {
      const response = await fetch(`/api/me/calls/export.csv?${params.toString()}`, { credentials: "include" });
      if (!response.ok) { const payload = await response.json().catch(() => ({})); throw { ...payload, status: response.status }; }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "gateway-calls.csv";
      anchor.click();
      URL.revokeObjectURL(url);
      notifyToast({ type: "success", message: "调用记录 CSV 已导出" });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setExporting(false);
    }
  }

  function changeRefreshInterval(value: string) {
    setRefreshInterval(value);
    const query = new URLSearchParams(window.location.search);
    if (value === "0") query.delete("refresh"); else query.set("refresh", value);
    window.history.replaceState(null, "", `/calls?${query.toString()}`);
  }

  async function openCall(requestId: string) {
    setDetailLoadingId(requestId);
    try {
      setSelectedCall(await api<GatewayCall>(`/api/me/calls/${requestId}`));
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setDetailLoadingId("");
    }
  }

  return (
    <PortalShell title="调用记录">
      <p className="page-intro">
        只展示当前账号通过平台 Gateway 发起的请求。请求正文、完整 API Key 和上游 Token 不写入调用日志；排查失败时复制 request_id 并提供给管理员。
      </p>

      <section className="panel call-filter-panel">
        <form className="call-filter-advanced-grid" onSubmit={submit}>
          <label className="form-field">
            request_id
            <input value={filters.requestId} onChange={(event) => setFilters((current) => ({ ...current, requestId: event.target.value }))} placeholder="精确定位一次调用" />
          </label>
          <label className="form-field">
            工具 slug
            <input value={filters.toolSlug} onChange={(event) => setFilters((current) => ({ ...current, toolSlug: event.target.value }))} placeholder="例如 tomodd" />
          </label>
          <label className="form-field">
            状态码
            <input value={filters.statusCode} onChange={(event) => setFilters((current) => ({ ...current, statusCode: event.target.value.replace(/\D/g, "").slice(0, 3) }))} placeholder="例如 502" inputMode="numeric" />
          </label>
          <label className="form-field">
            结果
            <select value={filters.result} onChange={(event) => setFilters((current) => ({ ...current, result: event.target.value }))}>
              <option value="">全部结果</option><option value="success">成功</option><option value="failed">失败</option><option value="rate_limited">限流</option>
            </select>
          </label>
          <label className="form-field">
            方法
            <select value={filters.method} onChange={(event) => setFilters((current) => ({ ...current, method: event.target.value }))}>
              <option value="">全部方法</option>{["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"].map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label className="form-field">
            API Key
            <select value={filters.keyId} onChange={(event) => setFilters((current) => ({ ...current, keyId: event.target.value }))}>
              <option value="">全部 Key</option>{keys.map((key) => <option key={key.id} value={key.id}>{key.label} · {key.prefix}...</option>)}
            </select>
          </label>
          <label className="form-field">开始时间<input type="datetime-local" value={filters.start} onChange={(event) => setFilters((current) => ({ ...current, start: event.target.value }))} /></label>
          <label className="form-field">结束时间<input type="datetime-local" value={filters.end} onChange={(event) => setFilters((current) => ({ ...current, end: event.target.value }))} /></label>
          <div className="call-filter-actions">
            <button className="button secondary compact" type="button" onClick={resetFilters} disabled={loading && !items.length}><FilterX size={14} />清空</button>
            <button className="button compact" type="submit" disabled={loading}><Search size={14} />{loading ? "查询中" : "查询"}</button>
          </div>
        </form>
      </section>

      <section className="panel call-list-panel">
        <div className="toolbar">
          <div>
            <h2>Gateway 请求</h2>
            <p className="muted">共 {total} 条匹配记录。实际保留周期见概览；CSV 单次最多 31 天、50,000 行。</p>
          </div>
          <div className="toolbar-actions">
            <label className="refresh-select">自动刷新<select value={refreshInterval} onChange={(event) => changeRefreshInterval(event.target.value)}><option value="0">关闭</option><option value="15">15 秒</option><option value="30">30 秒</option><option value="60">60 秒</option></select></label>
            <button className="button secondary compact" type="button" onClick={() => void exportCsv()} disabled={exporting}><Download size={14} />{exporting ? "导出中" : "导出 CSV"}</button>
            <button className="icon-button" type="button" title="刷新当前结果" aria-label="刷新当前结果" onClick={() => void load(page, appliedFilters, true)} disabled={loading}><RefreshCw size={16} /></button>
          </div>
        </div>
        {appliedFilters.requestId && (
          <div className="call-trace-note">
            <span>正在定位 request_id：<code>{appliedFilters.requestId}</code></span>
            <button className="text-icon-button" type="button" onClick={() => void copyToClipboard(appliedFilters.requestId, "request_id 已复制")}><Copy size={13} />复制给管理员</button>
          </div>
        )}
        {error && <div className="state-box error">{error}</div>}
        {loading && !items.length ? (
          <div className="state-box">正在加载调用记录...</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table call-history-table">
              <thead>
                <tr>
                  <th>时间 / request_id</th>
                  <th>工具与接口</th>
                  <th>结果</th>
                  <th>耗时</th>
                  <th>流量</th>
                  <th><span className="sr-only">操作</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.request_id}>
                    <td>
                      <strong>{new Date(item.created_at).toLocaleString()}</strong>
                      <code className="call-request-id">{item.request_id}</code>
                    </td>
                    <td className="title-cell">
                      <strong>{item.endpoint_summary || item.operation_id || item.path}</strong>
                      <span className={item.method === "GET" ? "status info" : item.method === "DELETE" ? "status warn" : "status good"}>{item.method}</span>
                      <code className="call-path">/gateway/{item.tool_slug}{item.path}</code>
                    </td>
                    <td>
                      <span className={statusTone(item.status_code)}>{item.status_code}</span>
                      <strong className="call-result-text">{item.error_code || "调用成功"}</strong>
                      <span className="muted">上游状态 {item.upstream_status_code ?? "-"}</span>
                    </td>
                    <td>{item.duration_ms} ms</td>
                    <td>请求 {formatBytes(item.request_bytes)}<br /><span className="muted">响应 {formatBytes(item.response_bytes)}</span></td>
                    <td>
                      <div className="toolbar-actions">
                        <button className="icon-button" type="button" title="查看详情" aria-label={`查看调用 ${item.request_id}`} disabled={detailLoadingId === item.request_id} onClick={() => void openCall(item.request_id)}><Eye size={15} /></button>
                        <button className="icon-button" type="button" title="复制 request_id" aria-label={`复制 request_id ${item.request_id}`} onClick={() => void copyToClipboard(item.request_id, "request_id 已复制")}><Copy size={15} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!items.length && !loading && (
                  <tr><td colSpan={6}><div className="state-box">{Object.values(appliedFilters).some(Boolean) ? "没有匹配的调用记录，请调整筛选条件。" : "暂无调用记录。请在自己的程序中携带 X-API-Key 调用 Gateway。"}</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <div className="audit-pagination" aria-label="调用记录分页">
          <span className="muted">第 {page} / {pageCount} 页，每页 {PAGE_SIZE} 条</span>
          <div className="toolbar-actions">
            <button className="button secondary compact" type="button" onClick={() => changePage(page - 1)} disabled={loading || page <= 1}><ChevronLeft size={14} />上一页</button>
            <button className="button secondary compact" type="button" onClick={() => changePage(page + 1)} disabled={loading || !hasMore}>下一页<ChevronRight size={14} /></button>
          </div>
        </div>
      </section>
      <CallDetailDrawer call={selectedCall} onClose={() => setSelectedCall(null)} />
    </PortalShell>
  );
}
