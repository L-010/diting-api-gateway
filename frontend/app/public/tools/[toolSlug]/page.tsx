"use client";

import Link from "next/link";
import { ArrowLeft, LogIn } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";

type PublicTool = {
  slug: string;
  name: string;
  description: string;
  published_endpoint_count: number;
  endpoints: { method: string; gateway_path: string; summary: string; description: string }[];
};

export default function PublicToolPage() {
  const params = useParams<{ toolSlug: string }>();
  const [tool, setTool] = useState<PublicTool | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [debugRetired, setDebugRetired] = useState(false);
  const [site, setSite] = useState({ site_name: "API Gateway", brand_image_url: "" });

  useEffect(() => {
    api<typeof site & { site_subtitle: string }>("/api/public/config").then(setSite).catch(() => undefined);
  }, []);
  useEffect(() => {
    setDebugRetired(new URLSearchParams(window.location.search).get("notice") === "debug-retired");
  }, []);
  useEffect(() => {
    setError("");
    api<PublicTool>(`/api/public/tools/${encodeURIComponent(params.toolSlug)}`)
      .then(setTool)
      .catch((cause) => setError(errorMessage(cause)));
  }, [attempt, params.toolSlug]);

  return (
    <main className="public-page">
      <nav className="public-nav">
        <Link href="/" className="public-brand">{site.brand_image_url ? <img className="public-brand-image" src={site.brand_image_url} alt="" /> : <span className="brand-mark">G</span>}{site.site_name}</Link>
        <div className="public-actions"><Link className="button secondary" href="/"><ArrowLeft size={15} />返回目录</Link><Link className="button" href="/login"><LogIn size={15} />登录</Link></div>
      </nav>
      <section className="public-section public-tool-detail">
        {debugRetired && <div className="notice">在线调试功能已下线。请登录后从接口文档复制 curl、Python 或 JavaScript 示例，在自己的程序中调用。</div>}
        {error && <div className="state-box error"><p>{error}</p><button className="button secondary compact" onClick={() => setAttempt((value) => value + 1)}>重试</button></div>}
        {!tool && !error && <div className="state-box">正在加载工具说明...</div>}
        {tool && <>
          <span className="tag">{tool.slug}</span>
          <h1>{tool.name}</h1>
          <p className="section-lead">{tool.description || "该工具暂无公开说明。"}</p>
          <section className="panel">
            <div className="toolbar"><h2>公开接口</h2><span className="status good">{tool.published_endpoint_count} 个可用</span></div>
            <div className="endpoint-list">{tool.endpoints.map((endpoint) => <div className="endpoint-row" key={`${endpoint.method}-${endpoint.gateway_path}`}><span className={endpoint.method === "GET" ? "status info" : "status good"}>{endpoint.method}</span><div><strong>{endpoint.summary}</strong><code className="endpoint-path">/gateway/{tool.slug}{endpoint.gateway_path}</code>{endpoint.description && <p className="muted">{endpoint.description}</p>}</div></div>)}</div>
            <p className="notice">登录后可查看完整参数、路由策略、错误码和可复制的程序集成示例；公开页面不执行 API 调用。</p>
          </section>
        </>}
      </section>
    </main>
  );
}
