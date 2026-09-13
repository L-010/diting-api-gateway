"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";

function VerifyEmailContent() {
  const token = useSearchParams().get("token") || "";
  const purpose = useSearchParams().get("purpose") || "registration";
  const [state, setState] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!token) { setState("error"); setMessage("验证链接缺少令牌。请使用邮件中的完整链接。"); return; }
    api(purpose === "change" ? "/api/auth/verify-email-change" : "/api/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) })
      .then(() => { setState("success"); setMessage(purpose === "change" ? "新邮箱已验证并生效。" : "邮箱验证成功，申请已进入管理员待审批队列。审批通过后即可登录。"); })
      .catch((cause) => { setState("error"); setMessage(errorMessage(cause)); });
  }, [purpose, token]);
  return <main className="auth-page"><section className="auth-panel"><span className="brand-mark">G</span><h1>邮箱验证</h1><p>验证令牌单次使用，重新签发后旧链接立即失效。</p></section><section className="auth-content"><div className="auth-card"><h2>{state === "loading" ? "正在验证..." : state === "success" ? "验证完成" : "无法完成验证"}</h2><div className={`state-box ${state === "error" ? "error" : state === "success" ? "success-state" : ""}`}>{message || "正在检查验证令牌，请稍候。"}</div><Link className="button" href={state === "success" ? "/login" : "/register"}>{state === "success" ? "前往登录" : "返回注册页"}</Link></div></section></main>;
}

export default function VerifyEmailPage() {
  return <Suspense fallback={<main className="route-guard" role="status">正在读取验证链接...</main>}><VerifyEmailContent /></Suspense>;
}
