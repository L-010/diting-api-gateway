"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

type RegisterResult = { username: string; approval_status: string };

export default function RegisterPage() {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [registrationEnabled, setRegistrationEnabled] = useState<boolean | null>(null);
  const [resending, setResending] = useState(false);
  const [resendMessage, setResendMessage] = useState("");
  const [site, setSite] = useState({ site_name: "API Gateway", site_subtitle: "开发者统一工作台", brand_image_url: "" });

  useEffect(() => {
    api<typeof site & { registration_enabled: boolean }>("/api/public/config")
      .then((payload) => { setRegistrationEnabled(payload.registration_enabled); setSite(payload); })
      .catch((cause) => { setRegistrationEnabled(false); setError(errorMessage(cause)); });
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setMessage("");
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await api<RegisterResult>("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          username: data.get("username"),
          email: data.get("email"),
          display_name: data.get("display_name"),
          password: data.get("password"),
          registration_note: data.get("registration_note"),
        }),
      });
      const message = `验证邮件已发送至你填写的邮箱。请在 24 小时内完成验证，之后申请才会进入管理员待审批队列。账号：${result.username}`;
      setMessage(message);
      notifyToast({ type: "success", message: "验证邮件已发送" });
      form.reset();
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setLoading(false);
    }
  }

  async function resend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (resending) return;
    setResending(true); setResendMessage("");
    try {
      const data = new FormData(event.currentTarget);
      await api("/api/auth/resend-verification", { method: "POST", body: JSON.stringify({ email: data.get("email") }) });
      setResendMessage("如果该邮箱存在尚未验证的申请，验证邮件会在稍后送达。");
    } catch (cause) { setResendMessage(errorMessage(cause)); }
    finally { setResending(false); }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel">
        {site.brand_image_url ? <img className="auth-brand-image" src={site.brand_image_url} alt="" /> : <span className="brand-mark">G</span>}
        <h1>
          注册申请
          <br />
          验证邮箱后进入审批
        </h1>
        <p>邮箱验证令牌 24 小时有效且只能使用一次。完成验证后，管理员才会看到待审批申请。</p>
      </section>
      <section className="auth-content">
        <form className="auth-card" onSubmit={submit}>
          <h2>提交开发者账号申请</h2>
          <p>请填写可接收验证邮件的真实邮箱和明确用途。</p>
          {registrationEnabled === false && <div className="state-box error">邮件服务未配置，公开注册当前关闭。已有账号和管理员创建的账号仍可登录。</div>}
          <label className="form-field">
            用户名
            <input name="username" placeholder="填写真实姓名，例如 张三" autoComplete="username" required minLength={1} maxLength={64} />
          </label>
          <label className="form-field">
            显示名称
            <input name="display_name" placeholder="例如 Alice / 地震实验室" maxLength={128} />
          </label>
          <label className="form-field">
            邮箱
            <input name="email" type="email" placeholder="name@example.com" autoComplete="email" maxLength={255} required />
          </label>
          <label className="form-field">
            密码
            <input name="password" type="password" autoComplete="new-password" required minLength={12} />
          </label>
          <label className="form-field">
            用途说明
            <textarea name="registration_note" placeholder="说明计划调用的工具、用途、预计调用频率等" maxLength={1000} />
          </label>
          <p className={`form-message ${message ? "success" : ""}`}>{error || message}</p>
          <button className="button" disabled={loading || registrationEnabled !== true} style={{ width: "100%" }}>
            {loading ? "正在提交..." : "提交申请"}
          </button>
          <p className="muted" style={{ marginTop: 18 }}>
            已有账号？<Link href="/login" style={{ color: "#1a5ec0" }}>去登录</Link>
            <br />
            <Link href="/" style={{ color: "#1a5ec0" }}>返回公开首页</Link>
          </p>
        </form>
        <form className="auth-card auth-secondary-card" onSubmit={resend}>
          <h2>重发验证邮件</h2>
          <p>仅用于已提交但尚未验证的申请。为保护账号信息，接口不会说明邮箱是否存在。</p>
          <label className="form-field">注册邮箱<input name="email" type="email" autoComplete="email" required /></label>
          {resendMessage && <p className="form-message success">{resendMessage}</p>}
          <button className="button secondary" disabled={resending || registrationEnabled !== true}>{resending ? "发送中..." : "重新发送"}</button>
        </form>
      </section>
    </main>
  );
}
