"use client";

import Link from "next/link";
import { Bell, CheckCheck, Circle } from "lucide-react";
import { useEffect, useState } from "react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

type Notification = { id: string; kind: string; title: string; body: string; action_url: string; read_at: string | null; created_at: string };
type Page = { items: Notification[]; unread_count: number; total: number; page: number; page_size: number; has_more: boolean };

export default function NotificationsPage() {
  const [data, setData] = useState<Page | null>(null); const [error, setError] = useState(""); const [loadingId, setLoadingId] = useState(""); const [unreadOnly, setUnreadOnly] = useState(false); const [attempt, setAttempt] = useState(0);
  useEffect(() => { setError(""); api<Page>(`/api/me/notifications?page=1&page_size=50&unread_only=${unreadOnly}`).then(setData).catch((cause) => setError(errorMessage(cause))); }, [attempt, unreadOnly]);
  async function markRead(item: Notification) { if (item.read_at || loadingId) return; setLoadingId(item.id); try { await api(`/api/me/notifications/${item.id}/read`, { method: "PATCH" }); setAttempt((value) => value + 1); } catch (cause) { notifyToast({ type: "error", message: errorMessage(cause) }); } finally { setLoadingId(""); } }
  async function markAll() { if (loadingId) return; setLoadingId("all"); try { await api("/api/me/notifications/read-all", { method: "PATCH" }); setAttempt((value) => value + 1); notifyToast({ type: "success", message: "全部通知已标为已读" }); } catch (cause) { notifyToast({ type: "error", message: errorMessage(cause) }); } finally { setLoadingId(""); } }
  return <PortalShell title="通知"><div className="toolbar"><p className="page-intro">审批结果、Key 临期、密码与账户安全变化会出现在这里。</p><div className="toolbar-actions"><label className="checkbox-field"><input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} /><span>只看未读</span></label><button className="button secondary compact" onClick={() => void markAll()} disabled={!data?.unread_count || Boolean(loadingId)}><CheckCheck size={15} />全部已读</button></div></div>{error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" onClick={() => setAttempt((value) => value + 1)}>重试</button></div>}{!data && !error && <div className="state-box">正在加载通知...</div>}<section className="panel notification-list">{data?.items.map((item) => <article className={item.read_at ? "notification-row" : "notification-row unread"} key={item.id}><button className="notification-read-button" title={item.read_at ? "已读" : "标为已读"} aria-label={item.read_at ? "已读" : `将 ${item.title} 标为已读`} disabled={Boolean(item.read_at) || Boolean(loadingId)} onClick={() => void markRead(item)}>{item.read_at ? <Bell size={17} /> : <Circle size={17} />}</button><div><div className="notification-heading"><strong>{item.title}</strong><time>{new Date(item.created_at).toLocaleString()}</time></div><p>{item.body}</p>{item.action_url && <Link href={item.action_url} onClick={() => void markRead(item)}>查看相关页面</Link>}</div></article>)}{data && !data.items.length && <div className="state-box">{unreadOnly ? "没有未读通知。" : "暂无通知。"}</div>}</section></PortalShell>;
}
