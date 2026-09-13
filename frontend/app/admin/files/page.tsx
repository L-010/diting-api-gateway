"use client";

import Link from "next/link";
import { ChevronLeft, ChevronRight, FileClock, Files, History, ListTodo, RefreshCw, Search } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { PortalShell } from "@/components/portal-shell";
import { RemoteFileTable } from "@/components/remote-file-table";
import { api, errorMessage } from "@/lib/api";
import { RemoteFilePage } from "@/lib/remote-files";
import { notifyToast } from "@/lib/toast";

type View = "tasks" | "files";
type FileFilters = { userId: string; tool: string; status: string; role: string; visibility: string; requestId: string; taskId: string; search: string; start: string; end: string };
type TaskFilters = { userId: string; tool: string; status: string; requestId: string; taskId: string; start: string; end: string };
type AdminTask = { id: string; tool_id: string; tool_slug: string; tool_name: string; owner_user_id: string; owner_username: string; upstream_id: string; source_request_id: string | null; status: string; capabilities: { files: boolean; download_all: boolean }; created_at: string; updated_at: string };
type AdminTaskPage = { items: AdminTask[]; total: number; page: number; page_size: number; has_more: boolean };

const emptyFiles: FileFilters = { userId: "", tool: "", status: "", role: "", visibility: "", requestId: "", taskId: "", search: "", start: "", end: "" };
const emptyTasks: TaskFilters = { userId: "", tool: "", status: "", requestId: "", taskId: "", start: "", end: "" };
function localTime(value: string | null) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16); }
function setTime(query: URLSearchParams, name: string, value: string) { if (value) query.set(name, new Date(value).toISOString()); }
function taskTone(status: string) { if (["succeeded", "completed", "success"].includes(status)) return "status good"; if (["failed", "cancelled", "canceled", "error"].includes(status)) return "status danger"; if (["running", "processing"].includes(status)) return "status info"; return "status warn"; }

export default function AdminFilesPage() {
  const [view, setView] = useState<View>("tasks");
  const [files, setFiles] = useState<RemoteFilePage | null>(null);
  const [tasks, setTasks] = useState<AdminTaskPage | null>(null);
  const [fileFilters, setFileFilters] = useState<FileFilters>(emptyFiles);
  const [appliedFileFilters, setAppliedFileFilters] = useState<FileFilters>(emptyFiles);
  const [taskFilters, setTaskFilters] = useState<TaskFilters>(emptyTasks);
  const [appliedTaskFilters, setAppliedTaskFilters] = useState<TaskFilters>(emptyTasks);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncingTaskId, setSyncingTaskId] = useState("");

  const loadFiles = useCallback(async (page: number, next: FileFilters) => {
    setLoading(true); setError("");
    const query = new URLSearchParams({ view: "files", page: String(page), page_size: "30" });
    ([ ["userId", "user_id"], ["tool", "tool_slug"], ["status", "status"], ["role", "role"], ["visibility", "visibility"], ["requestId", "source_request_id"], ["taskId", "task_id"], ["search", "search"] ] as Array<[keyof FileFilters, string]>).forEach(([key, name]) => { if (next[key]) query.set(name, next[key]); });
    setTime(query, "start", next.start); setTime(query, "end", next.end);
    window.history.replaceState(null, "", `/admin/files?${query}`);
    try { setFiles(await api<RemoteFilePage>(`/api/admin/files?${query}`)); setAppliedFileFilters(next); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setLoading(false); }
  }, []);

  const loadTasks = useCallback(async (page: number, next: TaskFilters) => {
    setLoading(true); setError("");
    const query = new URLSearchParams({ view: "tasks", page: String(page), page_size: "30" });
    ([ ["userId", "user_id"], ["tool", "tool_slug"], ["status", "status"], ["requestId", "source_request_id"], ["taskId", "task_id"] ] as Array<[keyof TaskFilters, string]>).forEach(([key, name]) => { if (next[key]) query.set(name, next[key]); });
    setTime(query, "start", next.start); setTime(query, "end", next.end);
    window.history.replaceState(null, "", `/admin/files?${query}`);
    try { setTasks(await api<AdminTaskPage>(`/api/admin/tasks?${query}`)); setAppliedTaskFilters(next); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const initialView: View = query.get("view") === "files" ? "files" : "tasks";
    const initialTasks: TaskFilters = { userId: query.get("user_id") || "", tool: query.get("tool_slug") || "", status: query.get("status") || "", requestId: query.get("source_request_id") || "", taskId: query.get("task_id") || "", start: localTime(query.get("start")), end: localTime(query.get("end")) };
    const initialFiles: FileFilters = { ...initialTasks, role: query.get("role") || "", visibility: query.get("visibility") || "", search: query.get("search") || "" };
    setView(initialView); setTaskFilters(initialTasks); setFileFilters(initialFiles);
    const page = Math.max(1, Number(query.get("page") || 1));
    if (initialView === "files") void loadFiles(page, initialFiles); else void loadTasks(page, initialTasks);
  }, [loadFiles, loadTasks]);

  function switchView(next: View) {
    if (next === view) return;
    setView(next); setError("");
    if (next === "files") void loadFiles(1, fileFilters); else void loadTasks(1, taskFilters);
  }
  function submitFiles(event: FormEvent) { event.preventDefault(); void loadFiles(1, fileFilters); }
  function submitTasks(event: FormEvent) { event.preventDefault(); void loadTasks(1, taskFilters); }
  async function retryTask(taskId: string) {
    if (syncingTaskId) return;
    setSyncingTaskId(taskId);
    try { await api(`/api/admin/tasks/${encodeURIComponent(taskId)}/sync`, { method: "POST" }); notifyToast({ type: "success", message: "任务文件同步已重新入队" }); }
    catch (cause) { notifyToast({ type: "error", message: "任务同步入队失败", details: errorMessage(cause) }); }
    finally { setSyncingTaskId(""); }
  }

  return <PortalShell admin title="任务与文件">
    <p className="page-intro">
      制品生命周期管理与存储追踪。用户程序生成的任务和文件制品，追踪从登记、同步、到可下载的全过程，并排查同步失败和存储问题。
    </p>
    <div className="profile-tabs file-view-tabs" role="tablist" aria-label="任务与文件视图">
      <button type="button" role="tab" aria-selected={view === "tasks"} className={view === "tasks" ? "active" : ""} onClick={() => switchView("tasks")}><ListTodo size={16}/>全局任务</button>
      <button type="button" role="tab" aria-selected={view === "files"} className={view === "files" ? "active" : ""} onClick={() => switchView("files")}><Files size={16}/>全局文件</button>
      <Link className="text-icon-button" href="/admin/file-downloads"><History size={16}/>下载访问记录</Link>
    </div>

    {view === "tasks" ? <>
      <section className="panel call-filter-panel"><form className="file-filter-grid" onSubmit={submitTasks}>
        <label className="form-field">用户 ID<input value={taskFilters.userId} onChange={event => setTaskFilters(value => ({ ...value, userId: event.target.value }))}/></label>
        <label className="form-field">工具<input value={taskFilters.tool} onChange={event => setTaskFilters(value => ({ ...value, tool: event.target.value }))} placeholder="slug"/></label>
        <label className="form-field">平台任务 ID<input value={taskFilters.taskId} onChange={event => setTaskFilters(value => ({ ...value, taskId: event.target.value }))}/></label>
        <label className="form-field">request_id<input value={taskFilters.requestId} onChange={event => setTaskFilters(value => ({ ...value, requestId: event.target.value }))}/></label>
        <label className="form-field">状态<select value={taskFilters.status} onChange={event => setTaskFilters(value => ({ ...value, status: event.target.value }))}><option value="">全部状态</option><option value="active">已登记</option><option value="queued">排队中</option><option value="running">运行中</option><option value="succeeded">已完成</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></label>
        <label className="form-field">开始<input type="datetime-local" value={taskFilters.start} onChange={event => setTaskFilters(value => ({ ...value, start: event.target.value }))}/></label>
        <label className="form-field">结束<input type="datetime-local" value={taskFilters.end} onChange={event => setTaskFilters(value => ({ ...value, end: event.target.value }))}/></label>
        <div className="call-filter-actions"><button type="button" className="button secondary compact" onClick={() => { setTaskFilters(emptyTasks); void loadTasks(1, emptyTasks); }}>清空</button><button className="button compact" disabled={loading}><Search size={14}/>查询</button></div>
      </form></section>
      <section className="panel"><div className="toolbar"><div><h2><ListTodo size={18}/>全局任务</h2><p className="muted">共 {tasks?.total || 0} 条；平台任务 ID 是后台排障和文件关联的统一标识。</p></div><button className="icon-button" aria-label="刷新任务" disabled={loading} onClick={() => void loadTasks(tasks?.page || 1, appliedTaskFilters)}><RefreshCw size={16}/></button></div>
        {error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" onClick={() => void loadTasks(tasks?.page || 1, appliedTaskFilters)}>重试</button></div>}
        {loading && !tasks ? <div className="state-box">正在加载全局任务...</div> : <div className="table-wrap"><table className="task-table"><thead><tr><th>所有者</th><th>工具</th><th>平台任务 ID</th><th>上游任务 ID</th><th>来源调用</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{tasks?.items.map(item => <tr key={item.id}><td><strong>{item.owner_username}</strong><small>{item.owner_user_id}</small></td><td><strong>{item.tool_name}</strong><small>{item.tool_slug}</small></td><td><code>{item.id}</code></td><td><code>{item.upstream_id}</code></td><td>{item.source_request_id ? <code>{item.source_request_id}</code> : <span className="muted">历史任务未记录</span>}</td><td><span className={taskTone(item.status)}>{item.status}</span></td><td>{new Date(item.updated_at).toLocaleString()}</td><td><div className="table-actions"><Link className="button secondary compact" href={`/admin/tasks/${item.id}`}>任务详情</Link><button type="button" className="button secondary compact" disabled={Boolean(syncingTaskId)} onClick={() => void retryTask(item.id)}>{syncingTaskId === item.id ? "入队中..." : "重试同步"}</button><button type="button" className="button secondary compact" onClick={() => { setFileFilters(value => ({ ...value, taskId: item.id })); setView("files"); void loadFiles(1, { ...fileFilters, taskId: item.id }); }}>查看文件</button></div></td></tr>)}{!tasks?.items.length && <tr><td colSpan={8}><div className="empty-table">没有匹配的任务。</div></td></tr>}</tbody></table></div>}
        <div className="audit-pagination"><span>第 {tasks?.page || 1} 页</span><div className="toolbar-actions"><button className="button secondary compact" disabled={loading || (tasks?.page || 1) <= 1} onClick={() => void loadTasks((tasks?.page || 1) - 1, appliedTaskFilters)}><ChevronLeft size={14}/>上一页</button><button className="button secondary compact" disabled={loading || !tasks?.has_more} onClick={() => void loadTasks((tasks?.page || 1) + 1, appliedTaskFilters)}>下一页<ChevronRight size={14}/></button></div></div>
      </section>
    </> : <>
      <section className="panel call-filter-panel"><form className="file-filter-grid" onSubmit={submitFiles}>
        <label className="form-field">用户 ID<input value={fileFilters.userId} onChange={event => setFileFilters(value => ({ ...value, userId: event.target.value }))}/></label><label className="form-field">工具<input value={fileFilters.tool} onChange={event => setFileFilters(value => ({ ...value, tool: event.target.value }))} placeholder="slug"/></label><label className="form-field">状态<select value={fileFilters.status} onChange={event => setFileFilters(value => ({ ...value, status: event.target.value }))}><option value="">全部状态</option><option value="ready">可下载</option><option value="pending">同步中</option><option value="delete_pending">删除中</option><option value="quarantined">已隔离</option><option value="error">异常</option></select></label><label className="form-field">文件角色<select value={fileFilters.role} onChange={event => setFileFilters(value => ({ ...value, role: event.target.value }))}><option value="">全部角色</option><option value="input">输入</option><option value="intermediate">过程</option><option value="output">结果</option><option value="log">日志</option><option value="archive">归档</option></select></label><label className="form-field">可见性<select value={fileFilters.visibility} onChange={event => setFileFilters(value => ({ ...value, visibility: event.target.value }))}><option value="">全部可见性</option><option value="user">用户可见</option><option value="admin">仅管理员</option><option value="internal">内部文件</option></select></label>
        <label className="form-field">request_id<input value={fileFilters.requestId} onChange={event => setFileFilters(value => ({ ...value, requestId: event.target.value }))}/></label><label className="form-field">平台任务 ID<input value={fileFilters.taskId} onChange={event => setFileFilters(value => ({ ...value, taskId: event.target.value }))}/></label><label className="form-field">文件名<input value={fileFilters.search} onChange={event => setFileFilters(value => ({ ...value, search: event.target.value }))}/></label><label className="form-field">开始<input type="datetime-local" value={fileFilters.start} onChange={event => setFileFilters(value => ({ ...value, start: event.target.value }))}/></label><label className="form-field">结束<input type="datetime-local" value={fileFilters.end} onChange={event => setFileFilters(value => ({ ...value, end: event.target.value }))}/></label><div className="call-filter-actions"><button type="button" className="button secondary compact" onClick={() => { setFileFilters(emptyFiles); void loadFiles(1, emptyFiles); }}>清空</button><button className="button compact" disabled={loading}><Search size={14}/>查询</button></div>
      </form></section>
      <section className="panel"><div className="toolbar"><div><h2><FileClock size={18}/>全局文件目录</h2><p className="muted">共 {files?.total || 0} 条</p></div><button className="icon-button" aria-label="刷新文件" disabled={loading} onClick={() => void loadFiles(files?.page || 1, appliedFileFilters)}><RefreshCw size={16}/></button></div>{error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" onClick={() => void loadFiles(files?.page || 1, appliedFileFilters)}>重试</button></div>}{loading && !files ? <div className="state-box">正在加载全局文件...</div> : <RemoteFileTable files={files?.items || []} admin onChanged={() => void loadFiles(files?.page || 1, appliedFileFilters)}/>}<div className="audit-pagination"><span>第 {files?.page || 1} 页</span><div className="toolbar-actions"><button className="button secondary compact" disabled={loading || (files?.page || 1) <= 1} onClick={() => void loadFiles((files?.page || 1) - 1, appliedFileFilters)}><ChevronLeft size={14}/>上一页</button><button className="button secondary compact" disabled={loading || !files?.has_more} onClick={() => void loadFiles((files?.page || 1) + 1, appliedFileFilters)}>下一页<ChevronRight size={14}/></button></div></div></section>
    </>}
    <p className="overview-footnote"><Link href="/admin/monitor">查看监控告警</Link>以排查持续同步失败；同步进程状态和任务来源 request_id 可用于定位后台链路。</p>
  </PortalShell>;
}
