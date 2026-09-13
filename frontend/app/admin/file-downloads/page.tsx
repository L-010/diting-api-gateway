"use client";

import Link from "next/link";
import { ArrowLeft, ChevronLeft, ChevronRight, Download, RefreshCw, Search } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { formatBytes } from "@/lib/calls";

type DownloadEvent = {
  id: string;
  remote_file_id: string;
  file_name: string;
  tool_slug: string;
  actor_user_id: string;
  actor_username: string;
  owner_user_id: string;
  owner_username: string;
  request_id: string;
  is_admin: boolean;
  status: string;
  bytes_sent: number;
  error_code: string;
  started_at: string;
  completed_at: string | null;
};

type DownloadEventPage = { items: DownloadEvent[]; total: number; page: number; page_size: number; has_more: boolean };
type Filters = { ownerId: string; actorId: string; tool: string; status: string; fileId: string; requestId: string; start: string; end: string };
const emptyFilters: Filters = { ownerId: "", actorId: "", tool: "", status: "", fileId: "", requestId: "", start: "", end: "" };

function localTime(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function eventTone(status: string) {
  if (status === "completed") return "status good";
  if (["failed", "interrupted"].includes(status)) return "status danger";
  return "status info";
}

function eventLabel(status: string) {
  return ({ started: "传输中", completed: "已完成", failed: "失败", interrupted: "已中断" } as Record<string, string>)[status] || status;
}

export default function AdminFileDownloadsPage() {
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [applied, setApplied] = useState<Filters>(emptyFilters);
  const [data, setData] = useState<DownloadEventPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (page: number, next: Filters) => {
    setLoading(true);
    setError("");
    const query = new URLSearchParams({ page: String(page), page_size: "30" });
    const mapping: Array<[keyof Filters, string]> = [["ownerId", "owner_user_id"], ["actorId", "actor_user_id"], ["tool", "tool_slug"], ["status", "status"], ["fileId", "file_id"], ["requestId", "request_id"]];
    mapping.forEach(([key, name]) => { if (next[key].trim()) query.set(name, next[key].trim()); });
    if (next.start) query.set("start", new Date(next.start).toISOString());
    if (next.end) query.set("end", new Date(next.end).toISOString());
    window.history.replaceState(null, "", `/admin/file-downloads?${query}`);
    try {
      setData(await api<DownloadEventPage>(`/api/admin/file-download-events?${query}`));
      setApplied(next);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const initial: Filters = {
      ownerId: query.get("owner_user_id") || "",
      actorId: query.get("actor_user_id") || "",
      tool: query.get("tool_slug") || "",
      status: query.get("status") || "",
      fileId: query.get("file_id") || "",
      requestId: query.get("request_id") || "",
      start: localTime(query.get("start")),
      end: localTime(query.get("end")),
    };
    setFilters(initial);
    void load(Math.max(1, Number(query.get("page") || 1)), initial);
  }, [load]);

  function submit(event: FormEvent) {
    event.preventDefault();
    void load(1, filters);
  }

  return <PortalShell admin title="文件下载访问记录">
    <div className="toolbar"><div><p className="page-intro">查看每次管理员或用户文件传输的开始、完成、中断与字节数。管理员下载同时写入审计日志。</p></div><Link className="button secondary compact" href="/admin/files"><ArrowLeft size={15}/>返回任务与文件</Link></div>
    <section className="panel call-filter-panel"><form className="file-filter-grid" onSubmit={submit}>
      <label className="form-field">文件所有者 ID<input value={filters.ownerId} onChange={event => setFilters(value => ({ ...value, ownerId: event.target.value }))}/></label>
      <label className="form-field">下载操作者 ID<input value={filters.actorId} onChange={event => setFilters(value => ({ ...value, actorId: event.target.value }))}/></label>
      <label className="form-field">工具<input value={filters.tool} onChange={event => setFilters(value => ({ ...value, tool: event.target.value }))} placeholder="slug"/></label>
      <label className="form-field">状态<select value={filters.status} onChange={event => setFilters(value => ({ ...value, status: event.target.value }))}><option value="">全部状态</option><option value="started">传输中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="interrupted">已中断</option></select></label>
      <label className="form-field">平台文件 ID<input value={filters.fileId} onChange={event => setFilters(value => ({ ...value, fileId: event.target.value }))}/></label>
      <label className="form-field">下载 request_id<input value={filters.requestId} onChange={event => setFilters(value => ({ ...value, requestId: event.target.value }))}/></label>
      <label className="form-field">开始时间<input type="datetime-local" value={filters.start} onChange={event => setFilters(value => ({ ...value, start: event.target.value }))}/></label>
      <label className="form-field">结束时间<input type="datetime-local" value={filters.end} onChange={event => setFilters(value => ({ ...value, end: event.target.value }))}/></label>
      <div className="call-filter-actions"><button className="button secondary compact" type="button" onClick={() => { setFilters(emptyFilters); void load(1, emptyFilters); }}>清空</button><button className="button compact" disabled={loading}><Search size={14}/>查询</button></div>
    </form></section>
    <section className="panel">
      <div className="toolbar"><div><h2><Download size={18}/>传输记录</h2><p className="muted">共 {data?.total || 0} 条。传输中记录超过租约后会由下一次分配自动标记为中断。</p></div><button className="icon-button" type="button" aria-label="刷新下载记录" disabled={loading} onClick={() => void load(data?.page || 1, applied)}><RefreshCw size={16} className={loading ? "spin" : undefined}/></button></div>
      {error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" type="button" onClick={() => void load(data?.page || 1, applied)}>重试</button></div>}
      {loading && !data ? <div className="state-box">正在加载下载记录...</div> : <div className="table-wrap"><table className="data-table"><thead><tr><th>开始时间</th><th>文件 / 工具</th><th>所有者</th><th>操作者</th><th>状态</th><th>已传输</th><th>request_id</th></tr></thead><tbody>{data?.items.map(item => <tr key={item.id}><td>{new Date(item.started_at).toLocaleString()}<small>{item.completed_at ? `完成于 ${new Date(item.completed_at).toLocaleString()}` : "尚未结束"}</small></td><td><strong title={item.file_name}>{item.file_name}</strong><small>{item.tool_slug} / <code>{item.remote_file_id}</code></small></td><td><strong>{item.owner_username}</strong><small>{item.owner_user_id}</small></td><td><strong>{item.actor_username}</strong><small>{item.is_admin ? "管理员" : "用户本人"}</small></td><td><span className={eventTone(item.status)}>{eventLabel(item.status)}</span><small>{item.error_code || "-"}</small></td><td>{formatBytes(item.bytes_sent)}</td><td><code>{item.request_id}</code></td></tr>)}{!data?.items.length && <tr><td colSpan={7}><div className="state-box">没有匹配的下载访问记录。</div></td></tr>}</tbody></table></div>}
      <div className="audit-pagination"><span>第 {data?.page || 1} 页</span><div className="toolbar-actions"><button className="button secondary compact" disabled={loading || (data?.page || 1) <= 1} onClick={() => void load((data?.page || 1) - 1, applied)}><ChevronLeft size={14}/>上一页</button><button className="button secondary compact" disabled={loading || !data?.has_more} onClick={() => void load((data?.page || 1) + 1, applied)}>下一页<ChevronRight size={14}/></button></div></div>
    </section>
  </PortalShell>;
}
