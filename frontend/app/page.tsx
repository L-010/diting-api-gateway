"use client";

import Link from "next/link";
import { ArrowRight, Check, LogIn, Menu, Moon, Radio, ShieldCheck, Sparkles, Sun, WalletCards, Waves, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type PublicConfig = { registration_enabled: boolean; password_recovery_enabled: boolean; support_contact: string; site_name: string; site_subtitle: string; brand_image_url: string };
type PublicTool = { slug: string; name: string; description: string; published_endpoint_count: number };
const count = (value: number) => new Intl.NumberFormat("zh-CN").format(value);

function GatewayScene({ endpointCount }: { endpointCount: number }) {
  return <div className="reference-scene" aria-label="统一地震数据网关示意图">
    <div className="reference-orbit orbit-one" /><div className="reference-orbit orbit-two" /><div className="reference-orbit orbit-three" />
    <div className="reference-node node-a"><span>波</span>波形</div><div className="reference-node node-b"><span>震</span>震情</div><div className="reference-node node-c"><span>数</span>数据</div>
    <div className="reference-core"><Sparkles size={18} /><strong>SEISMIC</strong><b>网关核心</b><small>{endpointCount ? `业务接口已准备` : "统一接入与审计"}</small></div>
    <div className="reference-terminal"><div className="reference-terminal-bar"><i /><i /><i /><span>gateway / request</span></div><div className="reference-terminal-code"><p><em>$</em> curl -X POST /gateway</p><p className="terminal-muted"># 统一路由 · 权限校验</p><p><strong>200 OK</strong> <span>{`{ "status": "ready" }`}</span></p><p><em>$</em> seismic --inspect</p></div></div>
  </div>;
}

export default function HomePage() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [tools, setTools] = useState<PublicTool[]>([]);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("dark");
  useEffect(() => { let active = true; Promise.allSettled([api<PublicConfig>("/api/public/config"), api<PublicTool[]>("/api/public/tools")]).then(([configResult, toolsResult]) => { if (!active) return; if (configResult.status === "fulfilled") setConfig(configResult.value); if (toolsResult.status === "fulfilled") setTools(toolsResult.value); }); return () => { active = false; }; }, []);
  useEffect(() => {
    const savedTheme = localStorage.getItem("api-gateway:home-theme") as "light" | "dark" | null;
    const prefersDark = matchMedia("(prefers-color-scheme: dark)").matches;
    const initialTheme = (savedTheme === "light" || savedTheme === "dark") ? savedTheme : (prefersDark ? "dark" : "light");
    setTheme(initialTheme);
    document.documentElement.dataset.homeTheme = initialTheme;
  }, []);
  const toggleTheme = () => {
    const newTheme = theme === "dark" ? "light" : "dark";
    setTheme(newTheme);
    localStorage.setItem("api-gateway:home-theme", newTheme);
    document.documentElement.dataset.homeTheme = newTheme;
  };
  const endpointCount = useMemo(() => tools.reduce((total, tool) => total + tool.published_endpoint_count, 0), [tools]);
  const closeMenu = () => setMobileOpen(false);
  return <main className="reference-home">
    <div className="reference-stars" aria-hidden="true"><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /></div>
    <header className="reference-nav"><Link className="reference-logo" href="/" onClick={closeMenu}>{config?.brand_image_url ? <img src={config.brand_image_url} alt="" /> : <span><Waves size={19} /></span>}<b>{config?.site_name || "SEISMIC API"}</b></Link><nav className={mobileOpen ? "reference-links open" : "reference-links"}><Link href="/tools" onClick={closeMenu}>工具目录</Link><Link href="/portal" onClick={closeMenu}>接入路径</Link><Link href="/api-keys" onClick={closeMenu}>安全边界</Link></nav><div className="reference-nav-actions"><button className="reference-icon" type="button" aria-label="切换主题" onClick={toggleTheme}>{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button><Link className="reference-console" href={config?.registration_enabled ? "/register" : "/login"}><span>A</span>控制台<ArrowRight size={14} /></Link><button className="reference-menu" type="button" aria-label={mobileOpen ? "关闭菜单" : "打开菜单"} onClick={() => setMobileOpen((open) => !open)}>{mobileOpen ? <X size={19} /> : <Menu size={19} />}</button></div></header>
    <section className="reference-hero"><div className="reference-copy"><div className="reference-overline"><span /> SEISMIC / REAL DATA GATEWAY</div><h1>一个网关<br /><span>只接真实数据。</span></h1><p>面向地震监测、研究与业务系统的统一 API 入口。清楚的权限、可靠的路由，以及每一次调用都可追溯。</p><div className="reference-actions">{config?.registration_enabled ? <Link className="reference-primary" href="/register">申请接入<ArrowRight size={18} /></Link> : <Link className="reference-primary" href="/login">进入控制台<ArrowRight size={18} /></Link>}<Link className="reference-secondary" href="/tools">阅读工具目录<ArrowRight size={17} /></Link></div><div className="reference-trust"><span><Check size={14} /> 真实数据</span><span><Check size={14} /> 原生客户端就绪</span><span><Check size={14} /> 全程审计</span></div></div><GatewayScene endpointCount={endpointCount} /></section>
    <section className="reference-facts" aria-label="平台能力概览"><article><span>一个 API</span><strong>统一</strong><p>主流地震能力聚合在一个干净的端点下。</p></article><article><span>真实数据</span><strong>可验证</strong><p>公开工具与接口状态清楚可见。</p></article><article><span>自动路由</span><strong>多个工具</strong><p>按业务需要进入对应的分析路径。</p></article><article><span>可审计</span><strong>按任务追溯</strong><p>调用记录、任务结果使用情况可追溯。</p></article></section>
  </main>;
}
