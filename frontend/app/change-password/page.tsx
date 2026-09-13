"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

export default function ChangePasswordPage() {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError("");
    setMessage("");
    setLoading(true);
    try {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: data.get("current"), new_password: data.get("next") }),
      });
      setMessage("密码已更新，正在进入开发者门户...");
      notifyToast({ type: "success", message: "密码已更新" });
      setTimeout(() => router.replace("/portal"), 700);
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel">
        <span className="brand-mark">G</span>
        <h1>
          首次登录
          <br />
          安全校验
        </h1>
        <p>CSV 批量开通的用户首次登录必须修改临时密码。改密完成前不能创建 API Key 或调用 Gateway。</p>
      </section>
      <section className="auth-content">
        <form className="auth-card" onSubmit={submit}>
          <h2>修改临时密码</h2>
          <p>新密码至少 12 位，必须包含大小写字母和数字，且不能包含用户名。</p>
          <label className="form-field">
            当前临时密码
            <input name="current" type="password" autoComplete="current-password" required />
          </label>
          <label className="form-field">
            新密码
            <input name="next" type="password" autoComplete="new-password" minLength={12} required />
          </label>
          <p className={`form-message ${message ? "success" : ""}`}>{message || error}</p>
          <button className="button" style={{ width: "100%" }} disabled={loading}>
            {loading ? "正在提交..." : "确认并继续"}
          </button>
        </form>
      </section>
    </main>
  );
}
