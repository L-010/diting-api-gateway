"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [enabled, setEnabled] = useState<boolean | null>(null); const [loading, setLoading] = useState(false); const [message, setMessage] = useState(""); const [error, setError] = useState("");
  useEffect(() => { api<{ password_recovery_enabled: boolean }>("/api/public/config").then((value) => setEnabled(value.password_recovery_enabled)).catch((cause) => { setEnabled(false); setError(errorMessage(cause)); }); }, []);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setLoading(true); setError(""); try { const data = new FormData(event.currentTarget); await api("/api/auth/forgot-password", { method: "POST", body: JSON.stringify({ email: data.get("email") }) }); setMessage("如果该邮箱对应可用账号，重置邮件将在稍后送达。链接 30 分钟有效。"); } catch (cause) { setError(errorMessage(cause)); } finally { setLoading(false); } }
  return <main className="auth-page"><section className="auth-panel"><span className="brand-mark">G</span><h1>找回密码</h1><p>重置令牌只保存哈希、单次使用，重新签发会使旧令牌失效。</p></section><section className="auth-content"><form className="auth-card" onSubmit={submit}><h2>发送重置邮件</h2>{enabled === false && <div className="state-box error">自助找回当前关闭，请联系管理员执行强制重置。</div>}<label className="form-field">验证邮箱<input name="email" type="email" autoComplete="email" required /></label>{(message || error) && <div className={`state-box ${error ? "error" : "success-state"}`}>{error || message}</div>}<button className="button" disabled={loading || enabled !== true}>{loading ? "发送中..." : "发送重置邮件"}</button><Link href="/login">返回登录</Link></form></section></main>;
}
