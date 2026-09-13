"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Ban, Clock3, LockKeyhole, UserRoundX } from "lucide-react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

type LoginResult = { roles: string[]; must_change_password: boolean };
type AccountFeedback = { code: string; title: string; message: string; reason?: string };

function accountFeedback(cause: unknown): AccountFeedback | null {
  if (!cause || typeof cause !== "object") return null;
  const error = cause as ApiError;
  const details = error.details && typeof error.details === "object" ? error.details as { reason?: unknown } : null;
  const reason = typeof details?.reason === "string" ? details.reason : undefined;
  const feedback: Record<string, Omit<AccountFeedback, "code" | "reason">> = {
    PENDING_APPROVAL: { title: "申请正在审核", message: "管理员审批通过后即可登录，无需重复提交注册申请。" },
    REGISTRATION_REJECTED: { title: "申请未通过", message: "请根据下方原因补充信息，并联系管理员重新确认。" },
    ACCOUNT_DISABLED: { title: "账号已停用", message: "现有登录会话和 API Key 均不可用，请联系管理员恢复访问。" },
    ACCOUNT_LOCKED: { title: "账号暂时锁定", message: "登录失败次数过多，请稍后再试；如非本人操作，请联系管理员。" },
    RATE_LIMITED: { title: "尝试次数过多", message: "请稍后再试，避免继续触发登录保护。" },
    EMAIL_NOT_VERIFIED: { title: "邮箱尚未验证", message: "请先打开注册验证邮件完成验证；链接过期时可在注册页重新提交验证邮件。" },
  };
  const matched = error.code ? feedback[error.code] : undefined;
  return matched && error.code ? { code: error.code, ...matched, reason } : null;
}

function AccountStateMessage({ feedback }: { feedback: AccountFeedback }) {
  const Icon = feedback.code === "PENDING_APPROVAL" ? Clock3 : feedback.code === "REGISTRATION_REJECTED" ? UserRoundX : feedback.code === "ACCOUNT_DISABLED" ? Ban : LockKeyhole;
  return (
    <section className="account-state-message" role="alert">
      <Icon size={20} />
      <div>
        <strong>{feedback.title}</strong>
        <p>{feedback.message}</p>
        {feedback.reason && <p className="account-state-reason"><span>管理员说明：</span>{feedback.reason}</p>}
      </div>
    </section>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState<AccountFeedback | null>(null);
  const [loading, setLoading] = useState(false);
  const [site, setSite] = useState({ site_name: "API Gateway", site_subtitle: "开发者统一工作台", brand_image_url: "" });
  const submittingRef = useRef(false);
  useEffect(() => { api<typeof site>("/api/public/config").then(setSite).catch(() => undefined); }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setLoading(true);
    setError("");
    setFeedback(null);
    const data = new FormData(event.currentTarget);
    try {
      const result = await api<LoginResult>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: data.get("username"), password: data.get("password") }),
      });
      router.replace(result.must_change_password ? "/change-password" : result.roles.includes("admin") ? "/admin" : "/portal");
    } catch (cause) {
      const message = errorMessage(cause);
      const nextFeedback = accountFeedback(cause);
      setFeedback(nextFeedback);
      setError(nextFeedback ? "" : message);
      if (!nextFeedback) notifyToast({ type: "error", message });
    } finally {
      submittingRef.current = false;
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel">
        {site.brand_image_url ? <img className="auth-brand-image" src={site.brand_image_url} alt="" /> : <span className="brand-mark">G</span>}
        <h1>
          {site.site_name}
          <br />
          {site.site_subtitle}
        </h1>
        <p>注册申请通过后，生成一个全局 API Key，在自己的程序里调用全部已发布工具接口。</p>
      </section>
      <section className="auth-content">
        <form className="auth-card" onSubmit={submit}>
          <h2>登录平台</h2>
          <p>使用账户 + 密码进行登录；未审批、已拒绝或已停用账号不能登录。</p>
          <label className="form-field">
            用户名
            <input name="username" autoComplete="username" required maxLength={64} />
          </label>
          <label className="form-field">
            密码
            <input name="password" type="password" autoComplete="current-password" required />
          </label>
          <p className="form-message">{error}</p>
          {feedback && <AccountStateMessage feedback={feedback} />}
          <button className="button" disabled={loading} style={{ width: "100%" }}>
            {loading ? "正在登录..." : "登录"}
          </button>
          <p className="muted" style={{ marginTop: 18 }}>
            没有账号？<Link href="/register" style={{ color: "#1a5ec0" }}>提交注册申请</Link>
            <br />
            <Link href="/forgot-password" style={{ color: "#1a5ec0" }}>忘记密码</Link>
            <br />
            <Link href="/" style={{ color: "#1a5ec0" }}>返回公开首页</Link>
          </p>
        </form>
      </section>
    </main>
  );
}
