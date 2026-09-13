"use client";

import Link from "next/link";
import { Download, RefreshCw, ShieldAlert, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { formatBytes } from "@/lib/calls";
import { fileRoleLabel, fileStatusLabel, fileStatusTone, RemoteFile } from "@/lib/remote-files";
import { notifyToast } from "@/lib/toast";

export function RemoteFileTable({ files, admin = false, onChanged }: { files: RemoteFile[]; admin?: boolean; onChanged?: () => void }) {
  const [busyId, setBusyId] = useState("");

  async function download(item: RemoteFile) {
    if (busyId || !item.downloadable) return;
    setBusyId(item.id);
    try {
      const prefix = admin ? "/api/admin/files" : "/api/me/files";
      const result = await api<{ download_url: string }>(`${prefix}/${item.id}/download-authorizations`, { method: "POST" });
      window.location.assign(result.download_url);
    } catch (cause) {
      notifyToast({ type: "error", message: "下载授权创建失败", details: errorMessage(cause) });
    } finally {
      window.setTimeout(() => setBusyId(""), 800);
    }
  }

  async function remove(item: RemoteFile) {
    if (admin || busyId || !item.deletable || !window.confirm(`确认删除“${item.file_name}”？删除会异步提交到工具服务器，且不可撤销。`)) return;
    setBusyId(item.id);
    try {
      await api(`/api/me/files/${item.id}/delete`, { method: "POST", body: JSON.stringify({ confirm: true }) });
      notifyToast({ type: "success", message: "删除请求已提交" });
      onChanged?.();
    } catch (cause) {
      notifyToast({ type: "error", message: "文件删除请求失败", details: errorMessage(cause) });
    } finally {
      setBusyId("");
    }
  }

  async function quarantine(item: RemoteFile) {
    if (!admin || busyId || !window.confirm(`确认隔离“${item.file_name}”？隔离后用户和管理员都不能下载。`)) return;
    setBusyId(item.id);
    try {
      await api(`/api/admin/files/${item.id}/quarantine`, { method: "POST", body: JSON.stringify({ reason: "管理员从文件视图执行安全隔离" }) });
      notifyToast({ type: "success", message: "文件已安全隔离" });
      onChanged?.();
    } catch (cause) {
      notifyToast({ type: "error", message: "文件隔离失败", details: errorMessage(cause) });
    } finally {
      setBusyId("");
    }
  }

  async function retrySync(item: RemoteFile) {
    if (!admin || busyId) return;
    setBusyId(item.id);
    try {
      await api(`/api/admin/files/${item.id}/retry-sync`, { method: "POST", body: JSON.stringify({ reason: "管理员从文件视图重新同步元信息" }) });
      notifyToast({ type: "success", message: "文件重新同步已入队" }); onChanged?.();
    } catch (cause) { notifyToast({ type: "error", message: "重新同步失败", details: errorMessage(cause) }); }
    finally { setBusyId(""); }
  }

  return <div className="table-wrap"><table className="data-table remote-file-table"><thead><tr>{admin && <th>所有者</th>}<th>文件</th><th>工具 / 来源</th><th>角色</th><th>大小</th><th>状态</th><th>保留至</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>
    {files.map((item) => <tr key={item.id}>{admin && <td><strong>{item.owner_username || "未知用户"}</strong><br/><code>{item.owner_user_id}</code></td>}<td><strong className="file-name" title={item.file_name}>{item.file_name}</strong><small>{item.content_type}</small></td><td><strong>{item.tool_name}</strong><small>{item.source_request_id ? <>调用 <code>{item.source_request_id}</code></> : item.parent_platform_id ? <>任务 <Link href={admin ? `/admin/tasks/${item.parent_platform_id}` : `/tasks/${item.parent_platform_id}`}><code>{item.parent_platform_id}</code></Link></> : "独立文件"}</small></td><td>{fileRoleLabel(item.role)}{item.is_bundle && <small>整包</small>}</td><td>{item.size_bytes === null ? "待同步" : formatBytes(item.size_bytes)}</td><td><span className={fileStatusTone(item.status)}>{fileStatusLabel(item.status)}</span></td><td>{item.expires_at ? new Date(item.expires_at).toLocaleString() : "未设置"}</td><td><div className="table-actions"><button className="icon-button" type="button" title="下载文件" aria-label={`下载 ${item.file_name}`} disabled={!item.downloadable || Boolean(busyId)} onClick={() => void download(item)}><Download size={15}/></button>{!admin && <button className="icon-button danger-button" type="button" title="删除文件" aria-label={`删除 ${item.file_name}`} disabled={!item.deletable || Boolean(busyId)} onClick={() => void remove(item)}><Trash2 size={15}/></button>}{admin && item.status !== "quarantined" && <button className="icon-button danger-button" type="button" title="安全隔离" aria-label={`隔离 ${item.file_name}`} disabled={Boolean(busyId)} onClick={() => void quarantine(item)}><ShieldAlert size={15}/></button>}{admin && ["quarantined","error","missing"].includes(item.status) && item.parent_platform_id && <button className="icon-button" type="button" title="重新同步" aria-label={`重新同步 ${item.file_name}`} disabled={Boolean(busyId)} onClick={()=>void retrySync(item)}><RefreshCw size={15}/></button>}</div></td></tr>)}
    {!files.length && <tr><td colSpan={admin ? 8 : 7}><div className="state-box">没有匹配的文件与制品。</div></td></tr>}
  </tbody></table></div>;
}
