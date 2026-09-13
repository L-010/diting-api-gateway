"use client";

import Link from "next/link";
import { FormEvent, Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, errorMessage } from "@/lib/api";

function ResetPasswordContent() {
  const token = useSearchParams().get("token") || ""; const [loading, setLoading] = useState(false); const [message, setMessage] = useState(""); const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); const password = String(data.get("password") || ""); if (password !== data.get("confirm_password")) { setError("两次输入的密码不一致"); return; } setLoading(true); setError(""); try { await api("/api/auth/reset-password", { method: "POST", body: JSON.stringify({ token, new_password: password }) }); setMessage("密码已重置，所有旧登录态已失效。现在可使用新密码登录。"); } catch (cause) { setError(errorMessage(cause)); } finally { setLoading(false); } }
  return <main className="auth-page"><section className="auth-panel"><span className="brand-mark">G</span><h1>设置新密码</h1><p>密码重置会使现有登录态失效，但不会删除 API Key。</p></section><section className="auth-content"><form className="auth-card" onSubmit={submit}><h2>重置密码</h2>{!token && <div className="state-box error">重置链接缺少令牌。</div>}{!message && <><label className="form-field">新密码<input name="password" type="password" autoComplete="new-password" minLength={12} required /></label><label className="form-field">确认新密码<input name="confirm_password" type="password" autoComplete="new-password" minLength={12} required /></label></>}{(message || error) && <div className={`state-box ${error ? "error" : "success-state"}`}>{error || message}</div>}{!message && <button className="button" disabled={loading || !token}>{loading ? "重置中..." : "确认重置"}</button>}<Link href="/login">返回登录</Link></form></section></main>;
}

export default function ResetPasswordPage() {
  return <Suspense fallback={<main className="route-guard" role="status">正在读取重置链接...</main>}><ResetPasswordContent /></Suspense>;
}
