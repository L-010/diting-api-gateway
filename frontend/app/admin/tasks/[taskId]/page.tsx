"use client";

import Link from "next/link";
import { ArrowLeft, RefreshCw, UserRound } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PortalShell } from "@/components/portal-shell";
import { RemoteFileTable } from "@/components/remote-file-table";
import { api, errorMessage } from "@/lib/api";
import { RemoteFile } from "@/lib/remote-files";

type AdminTask = {
  id: string;
  tool_slug: string;
  tool_name: string;
  upstream_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
  capabilities: Record<string, boolean>;
  is_terminal: boolean;
  owner_user_id: string;
  owner_username: string;
  source_request_id: string | null;
  storage_endpoint_revision: number | null;
};

type Tab = "overview" | "logs" | "manifest" | "artifacts" | "files";

export default function AdminTaskDetailPage() {
  const taskId = useParams<{ taskId: string }>().taskId;
  const [task, setTask] = useState<AdminTask | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [payload, setPayload] = useState<unknown>(null);
  const [files, setFiles] = useState<RemoteFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadTask = useCallback(async () => {
    setError("");
    try {
      setTask(await api<AdminTask>(`/api/admin/tasks/${encodeURIComponent(taskId)}`));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  const loadTab = useCallback(async (next: Tab) => {
    if (next === "overview") return;
    setLoading(true);
    setError("");
    try {
      if (next === "files") {
        setFiles(await api<RemoteFile[]>(`/api/admin/tasks/${encodeURIComponent(taskId)}/files`));
      } else {
        setPayload(await api(`/api/admin/tasks/${encodeURIComponent(taskId)}/capabilities/${next}`));
      }
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("tab") as Tab | null;
    const selected = initial && ["overview", "logs", "manifest", "artifacts", "files"].includes(initial) ? initial : "overview";
    setTab(selected);
    void loadTask();
    if (selected !== "overview") void loadTab(selected);
  }, [loadTab, loadTask]);

  useEffect(() => {
    if (!task || task.is_terminal) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void loadTask();
      if (tab !== "overview") void loadTab(tab);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [loadTab, loadTask, tab, task]);

  function openTab(next: Tab) {
    setTab(next);
    setPayload(null);
    window.history.replaceState(null, "", `/admin/tasks/${encodeURIComponent(taskId)}?tab=${next}`);
    void loadTab(next);
  }

  const tabs = ([
    ["overview", "概览", true],
    ["logs", "日志", Boolean(task?.capabilities.logs)],
    ["manifest", "Manifest", Boolean(task?.capabilities.manifest)],
    ["artifacts", "上游制品响应", Boolean(task?.capabilities.artifacts)],
    ["files", "平台文件", true],
  ] as Array<[Tab, string, boolean]>).filter(([, , enabled]) => enabled);

  return <PortalShell admin title="任务详情">
    <div className="toolbar">
      <Link className="button secondary compact" href="/admin/files?view=tasks"><ArrowLeft size={15}/>返回全局任务</Link>
      <button className="icon-button" type="button" title="刷新任务" aria-label="刷新任务" disabled={loading} onClick={() => { void loadTask(); if (tab !== "overview") void loadTab(tab); }}><RefreshCw size={16} className={loading ? "spin" : undefined}/></button>
    </div>
    {error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" type="button" onClick={() => { void loadTask(); if (tab !== "overview") void loadTab(tab); }}>重试</button></div>}
    {!task && !error && <div className="state-box">正在加载任务...</div>}
    {task && <>
      <section className="panel task-summary">
        <div><span className="tag">{task.tool_slug}</span><h2>{task.tool_name}</h2><code>{task.id}</code></div>
        <dl>
          <div><dt>所有者</dt><dd><Link href={`/admin/users/${task.owner_user_id}`}><UserRound size={14}/>{task.owner_username}</Link></dd></div>
          <div><dt>状态</dt><dd><span className="status info">{task.status}</span></dd></div>
          <div><dt>存储端点版本</dt><dd>{task.storage_endpoint_revision ? `v${task.storage_endpoint_revision}` : "历史任务未绑定"}</dd></div>
        </dl>
      </section>
      <div className="profile-tabs" role="tablist" aria-label="任务详情视图">{tabs.map(([value, label]) => <button type="button" role="tab" aria-selected={tab === value} className={tab === value ? "active" : ""} key={value} onClick={() => openTab(value)}>{label}</button>)}</div>
      <section className="panel task-payload">
        {tab === "overview" ? <dl className="profile-facts">
          <div><dt>平台任务 ID</dt><dd><code>{task.id}</code></dd></div>
          <div><dt>上游任务 ID</dt><dd><code>{task.upstream_id}</code></dd></div>
          <div><dt>来源 request_id</dt><dd>{task.source_request_id ? <code>{task.source_request_id}</code> : "历史任务未记录"}</dd></div>
          <div><dt>创建时间</dt><dd>{new Date(task.created_at).toLocaleString()}</dd></div>
          <div><dt>更新时间</dt><dd>{new Date(task.updated_at).toLocaleString()}</dd></div>
          <div><dt>可用能力</dt><dd>{Object.entries(task.capabilities).filter(([, enabled]) => enabled).map(([name]) => name).join(" / ") || "仅平台记录"}</dd></div>
        </dl> : loading ? <div className="state-box">正在加载...</div> : tab === "files" ? <RemoteFileTable files={files} admin onChanged={() => void loadTab("files")}/> : <pre className="code-block">{JSON.stringify(payload, null, 2)}</pre>}
      </section>
    </>}
  </PortalShell>;
}
