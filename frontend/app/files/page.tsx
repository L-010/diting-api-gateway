"use client";

import { ChevronLeft, ChevronRight, FilterX, HardDrive, RefreshCw, Search } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { PortalShell } from "@/components/portal-shell";
import { RemoteFileTable } from "@/components/remote-file-table";
import { api, errorMessage } from "@/lib/api";
import { formatBytes } from "@/lib/calls";
import { RemoteFilePage } from "@/lib/remote-files";

type Filters = { search: string; tool: string; role: string; status: string; sourceRequestId: string; taskId: string; start: string; end: string };
const empty: Filters = { search: "", tool: "", role: "", status: "", sourceRequestId: "", taskId: "", start: "", end: "" };
function localTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16); }

export default function FilesPage() {
  const [data, setData] = useState<RemoteFilePage | null>(null);
  const [filters, setFilters] = useState<Filters>(empty);
  const [applied, setApplied] = useState<Filters>(empty);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (page: number, next: Filters, silent = false) => {
    if (!silent) setLoading(true);
    setError("");
    const query = new URLSearchParams({ page: String(page), page_size: "30" });
    const mapping: Array<[keyof Filters, string]> = [["search", "search"], ["tool", "tool_slug"], ["role", "role"], ["status", "status"], ["sourceRequestId", "source_request_id"], ["taskId", "task_id"]];
    mapping.forEach(([key, name]) => { if (next[key]) query.set(name, next[key]); });
    if (next.start) query.set("start", new Date(next.start).toISOString());
    if (next.end) query.set("end", new Date(next.end).toISOString());
    window.history.replaceState(null, "", `/files?${query}`);
    try { setData(await api<RemoteFilePage>(`/api/me/files?${query}`)); setApplied(next); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const initial: Filters = { search: query.get("search") || "", tool: query.get("tool_slug") || "", role: query.get("role") || "", status: query.get("status") || "", sourceRequestId: query.get("source_request_id") || "", taskId: query.get("task_id") || "", start: query.get("start") ? localTime(query.get("start") as string) : "", end: query.get("end") ? localTime(query.get("end") as string) : "" };
    setFilters(initial); void load(Math.max(1, Number(query.get("page") || 1)), initial);
  }, [load]);

  function submit(event: FormEvent) { event.preventDefault(); void load(1, filters); }
  const quotaPercent = data?.quota_bytes ? Math.min(100, data.used_bytes / data.quota_bytes * 100) : 0;
  return <PortalShell title="文件与制品">
    <p className="page-intro">通过平台统一网关调用工具后，产生的过程性文件。</p>
    <section className="panel file-quota-panel"><div><HardDrive size={20}/><span><strong>{formatBytes(data?.used_bytes || 0)}</strong><small>已用逻辑容量</small></span></div><div className="quota-summary"><span>{data?.quota_bytes ? `${formatBytes(data.quota_bytes)} 配额` : "配额加载中"}</span><div className="quota-track"><span style={{ width: `${quotaPercent}%` }}/></div></div></section>
    <section className="panel call-filter-panel"><form className="file-filter-grid" onSubmit={submit}>
      <label className="form-field">文件名<input value={filters.search} onChange={(e) => setFilters(v => ({...v, search:e.target.value}))} placeholder="输入文件名"/></label><label className="form-field">工具<input value={filters.tool} onChange={(e) => setFilters(v => ({...v, tool:e.target.value}))} placeholder="工具 slug"/></label><label className="form-field">角色<select value={filters.role} onChange={(e) => setFilters(v => ({...v, role:e.target.value}))}><option value="">全部角色</option><option value="input">输入</option><option value="intermediate">过程</option><option value="output">结果</option><option value="log">日志</option><option value="archive">归档</option></select></label><label className="form-field">状态<select value={filters.status} onChange={(e) => setFilters(v => ({...v, status:e.target.value}))}><option value="">全部状态</option><option value="ready">可下载</option><option value="pending">同步中</option><option value="delete_pending">删除中</option><option value="expired">已过期</option><option value="missing">上游已清理</option><option value="error">异常</option></select></label>
      <label className="form-field">request_id<input value={filters.sourceRequestId} onChange={(e) => setFilters(v => ({...v, sourceRequestId:e.target.value}))} placeholder="关联调用"/></label><label className="form-field">平台任务 ID<input value={filters.taskId} onChange={(e) => setFilters(v => ({...v, taskId:e.target.value}))} placeholder="关联任务"/></label><label className="form-field">开始<input type="datetime-local" value={filters.start} onChange={(e) => setFilters(v => ({...v, start:e.target.value}))}/></label><label className="form-field">结束<input type="datetime-local" value={filters.end} onChange={(e) => setFilters(v => ({...v, end:e.target.value}))}/></label>
      <div className="call-filter-actions"><button type="button" className="button secondary compact" onClick={() => { setFilters(empty); void load(1, empty); }}><FilterX size={14}/>清空</button><button className="button compact" disabled={loading}><Search size={14}/>查询</button></div>
    </form></section>
    <section className="panel"><div className="toolbar"><div><h2>我的文件</h2><p className="muted">共 {data?.total || 0} 条；只有状态为“可下载”的本人文件可以下载。</p></div><button className="icon-button" aria-label="刷新文件" disabled={loading} onClick={() => void load(data?.page || 1, applied, true)}><RefreshCw size={16}/></button></div>{error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" onClick={() => void load(data?.page || 1, applied)}>重试</button></div>}{loading && !data ? <div className="state-box">正在加载文件...</div> : <RemoteFileTable files={data?.items || []} onChanged={() => void load(data?.page || 1, applied, true)}/>}<div className="audit-pagination"><span>第 {data?.page || 1} 页</span><div className="toolbar-actions"><button className="button secondary compact" disabled={loading || (data?.page || 1) <= 1} onClick={() => void load((data?.page || 1)-1, applied)}><ChevronLeft size={14}/>上一页</button><button className="button secondary compact" disabled={loading || !data?.has_more} onClick={() => void load((data?.page || 1)+1, applied)}>下一页<ChevronRight size={14}/></button></div></div></section>
  </PortalShell>;
}
