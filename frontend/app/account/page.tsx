"use client";

import { FormEvent, useEffect, useState } from "react";
import { KeyRound, Mail, ShieldCheck, UserRound } from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

type Me = { id: string; username: string; display_name: string | null; email: string | null; email_verified: boolean; roles: string[]; approval_status: string };
type EventPage = { items: { id: string; action: string; actor_username: string; request_id: string; detail: Record<string, unknown>; created_at: string }[] };

export default function AccountPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [events, setEvents] = useState<EventPage | null>(null);
  const [emailChangeEnabled, setEmailChangeEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [eventsError, setEventsError] = useState("");
  const [emailConfigError, setEmailConfigError] = useState("");
  const [busy, setBusy] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setError("");
    setEventsError("");
    setEmailConfigError("");
    api<Me>("/api/me")
      .then((current) => {
        setMe(current);
        setDisplayName(current.display_name || "");
      })
      .catch((cause) => setError(errorMessage(cause)));
    api<EventPage>("/api/me/events?page=1&page_size=20")
      .then(setEvents)
      .catch((cause) => {
        setEvents(null);
        setEventsError(errorMessage(cause));
      });
    api<{ email_change_enabled: boolean }>("/api/public/config")
      .then((config) => setEmailChangeEnabled(config.email_change_enabled))
      .catch((cause) => {
        setEmailChangeEnabled(null);
        setEmailConfigError(errorMessage(cause));
      });
  }, [attempt]);

  async function profile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !me) return;
    setBusy("profile");
    try {
      const current = await api<Me>("/api/me/profile", {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName }),
      });
      setMe(current);
      setDisplayName(current.display_name || "");
      notifyToast({ type: "success", message: "显示名称已更新" });
    } catch (cause) {
      notifyToast({ type: "error", message: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function email(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || emailChangeEnabled !== true) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("email");
    try {
      await api("/api/me/change-email", { method: "POST", body: JSON.stringify({ email: form.get("email") }) });
      notifyToast({ type: "success", message: "验证邮件已发送到新邮箱" });
      formElement.reset();
    } catch (cause) {
      notifyToast({ type: "error", message: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function password(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    if (form.get("new_password") !== form.get("confirm_password")) {
      notifyToast({ type: "error", message: "两次输入的新密码不一致" });
      return;
    }
    setBusy("password");
    try {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: form.get("current_password"), new_password: form.get("new_password") }),
      });
      notifyToast({ type: "success", message: "密码已更新，其它登录态已失效" });
      formElement.reset();
      setAttempt((value) => value + 1);
    } catch (cause) {
      notifyToast({ type: "error", message: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  return (
    <PortalShell title="账户设置">
      <p className="page-intro">管理个人资料、验证邮箱和密码；安全事件仅返回本人及本人 Key 的脱敏白名单字段。</p>
      {error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" onClick={() => setAttempt((value) => value + 1)}>重试</button></div>}
      <div className="account-grid">
        <form className="panel" onSubmit={profile}>
          <UserRound />
          <h2>个人资料</h2>
          <label className="form-field">用户名<input value={me?.username || ""} disabled /></label>
          <label className="form-field">显示名称<input name="display_name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={128} disabled={!me} /></label>
          <button className="button" disabled={!me || Boolean(busy)}>{busy === "profile" ? "保存中" : "保存资料"}</button>
        </form>
        <form className="panel" onSubmit={email}>
          <Mail />
          <h2>登录邮箱</h2>
          <div className="signal-card"><strong>{me?.email || "未设置"}</strong><span>{me?.email_verified ? "已验证" : "未验证"}</span></div>
          {emailChangeEnabled === false && <div className="state-box">邮件服务未配置，暂时不能自助更换邮箱，请联系管理员。</div>}
          {emailConfigError && <div className="state-box error">邮件能力状态加载失败：{emailConfigError}</div>}
          <label className="form-field">新邮箱<input name="email" type="email" required disabled={emailChangeEnabled !== true || Boolean(busy)} /></label>
          <button className="button" disabled={!me || emailChangeEnabled !== true || Boolean(busy)}>{busy === "email" ? "发送中" : "发送验证邮件"}</button>
        </form>
        <form className="panel" onSubmit={password}>
          <KeyRound />
          <h2>修改密码</h2>
          <label className="form-field">当前密码<input name="current_password" type="password" autoComplete="current-password" required /></label>
          <label className="form-field">新密码<input name="new_password" type="password" autoComplete="new-password" minLength={12} required /></label>
          <label className="form-field">确认新密码<input name="confirm_password" type="password" autoComplete="new-password" minLength={12} required /></label>
          <button className="button" disabled={Boolean(busy)}>{busy === "password" ? "更新中" : "更新密码"}</button>
        </form>
      </div>
      <section className="panel" style={{ marginTop: 20 }}>
        <div className="toolbar"><div><h2><ShieldCheck size={18} /> 本人安全事件</h2><p className="muted">不显示邮件令牌、密码、完整 Key、IP、上游地址或凭据。</p></div></div>
        <div className="profile-event-list">
          {events?.items.map((item) => <div className="profile-event-row" key={item.id}><span className="event-marker"><ShieldCheck size={15} /></span><div><div className="event-heading"><strong>{item.action}</strong><time>{new Date(item.created_at).toLocaleString()}</time></div><p>操作人：{item.actor_username} · request_id：{item.request_id || "-"}</p>{Object.keys(item.detail).length > 0 && <p>{JSON.stringify(item.detail)}</p>}</div></div>)}
          {!events && !eventsError && <div className="state-box">正在加载安全事件...</div>}
          {eventsError && <div className="state-box error"><p>{eventsError}</p><button className="button secondary compact" onClick={() => setAttempt((value) => value + 1)}>重试</button></div>}
          {events && !events.items.length && <div className="state-box">暂无安全事件。</div>}
        </div>
      </section>
    </PortalShell>
  );
}
