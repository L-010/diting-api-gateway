"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Activity, AlertTriangle, ArrowRight, CircleUserRound, RefreshCw, Route, ServerCrash, ShieldCheck, Wrench } from "lucide-react";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

type DashboardTool = {
  id: string;
  slug: string;
  name: string;
  health?: { reachable?: boolean; status?: string; status_code?: number | null } | null;
  published_count: number;
  blocked_count: number;
};

type UserOverview = {
  stats: { total: number; pending: number; active: number; disabled: number; rejected: number };
};

type MonitorMetrics = {
  window: { minutes: number };
  requests: { total: number; failed: number; error_rate: number };
  alerts: { count: number; critical_count: number };
};

type MonitorAlert = {
  id: string;
  severity: "critical" | "warning" | "info";
  title: string;
  message: string;
  tool_slug?: string | null;
  request_id?: string | null;
  last_seen_at?: string | null;
};

function alertTone(severity: MonitorAlert["severity"]) {
  if (severity === "critical") return "status danger";
  if (severity === "warning") return "status warn";
  return "status info";
}

export default function AdminOverviewPage() {
  const [tools, setTools] = useState<DashboardTool[]>([]);
  const [users, setUsers] = useState<UserOverview | null>(null);
  const [metrics, setMetrics] = useState<MonitorMetrics | null>(null);
  const [alerts, setAlerts] = useState<MonitorAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  async function load(showToast = false) {
    setLoading(true);
    setError("");
    try {
      const [nextTools, nextUsers, nextMetrics, nextAlerts] = await Promise.all([
        api<DashboardTool[]>("/api/admin/dashboard/tools"),
        api<UserOverview>("/api/admin/users/overview?state=all&page=1&page_size=10"),
        api<MonitorMetrics>("/api/admin/monitor/metrics"),
        api<MonitorAlert[]>("/api/admin/monitor/alerts"),
      ]);
      setTools(nextTools);
      setUsers(nextUsers);
      setMetrics(nextMetrics);
      setAlerts(nextAlerts);
      setUpdatedAt(new Date());
      if (showToast) notifyToast({ type: "success", message: "管理总览已刷新" });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      if (showToast) notifyToast({ type: "error", message });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const unreachableTools = tools.filter((item) => item.health?.status === "unavailable");
  const blockedEndpoints = tools.reduce((sum, item) => sum + item.blocked_count, 0);
  const pendingUsers = users?.stats.pending ?? 0;
  const criticalAlerts = metrics?.alerts.critical_count ?? 0;
  const hasWork = pendingUsers > 0 || criticalAlerts > 0 || unreachableTools.length > 0 || blockedEndpoints > 0;
  const prioritizedTools = [...tools]
    .sort((left, right) => Number(left.health?.status !== "unavailable") - Number(right.health?.status !== "unavailable") || right.blocked_count - left.blocked_count)
    .slice(0, 8);

  return (
    <PortalShell admin title="管理总览">
      <div className="overview-heading">
        <p className="page-intro">集中查看待处理用户、严重告警、上游健康和接口阻断，再进入对应工作台完成处置。</p>
        <div className="overview-refresh">
          <span>{updatedAt ? `更新于 ${updatedAt.toLocaleTimeString()}` : loading ? "正在加载" : "尚未更新"}</span>
          <button className="icon-text-button" type="button" onClick={() => void load(true)} disabled={loading}>
            <RefreshCw size={15} className={loading ? "spin" : undefined} />
            <span>{loading ? "刷新中" : "刷新"}</span>
          </button>
        </div>
      </div>

      {error && <div className="state-box error" role="alert">{error}</div>}

      <div className="metrics overview-metrics" aria-busy={loading}>
        <Metric label="待审批用户" value={loading ? "-" : String(pendingUsers)} caption="需要确认访问资格" tone={pendingUsers ? "amber" : "green"} />
        <Metric label="严重告警" value={loading ? "-" : String(criticalAlerts)} caption={`最近 ${metrics?.window.minutes ?? 60} 分钟`} tone={criticalAlerts ? "red" : "green"} />
        <Metric label="上游不可达" value={loading ? "-" : String(unreachableTools.length)} caption={`共 ${tools.length} 个工具`} tone={unreachableTools.length ? "red" : "green"} />
        <Metric label="接口阻断" value={loading ? "-" : String(blockedEndpoints)} caption="需要治理后才能发布" tone={blockedEndpoints ? "red" : "green"} />
      </div>

      <div className="overview-grid">
        <section className="panel overview-action-panel">
          <div className="toolbar">
            <div>
              <h2>需要处理</h2>
              <p className="muted">按影响范围排列，只展示当前需要管理员介入的事项。</p>
            </div>
            <ShieldCheck size={22} />
          </div>
          {!loading && !hasWork && <div className="state-box success-state">当前没有待处理事项。</div>}
          {criticalAlerts > 0 && (
            <Link href="/admin/monitor" className="overview-action-row critical">
              <AlertTriangle size={18} />
              <span><strong>{criticalAlerts} 条严重告警</strong><small>立即查看失败调用和异常 Key</small></span>
              <ArrowRight size={17} />
            </Link>
          )}
          {unreachableTools.length > 0 && (
            <Link href="/admin/tools" className="overview-action-row critical">
              <ServerCrash size={18} />
              <span><strong>{unreachableTools.length} 个上游不可达</strong><small>{unreachableTools.slice(0, 3).map((item) => item.name).join("、")}</small></span>
              <ArrowRight size={17} />
            </Link>
          )}
          {pendingUsers > 0 && (
            <Link href="/admin/users" className="overview-action-row">
              <CircleUserRound size={18} />
              <span><strong>{pendingUsers} 个用户待审批</strong><small>核对用途并确认访问资格</small></span>
              <ArrowRight size={17} />
            </Link>
          )}
          {blockedEndpoints > 0 && (
            <Link href="/admin/tools" className="overview-action-row">
              <Route size={18} />
              <span><strong>{blockedEndpoints} 个接口被阻断</strong><small>检查路径冲突、删除和高风险变化</small></span>
              <ArrowRight size={17} />
            </Link>
          )}
        </section>

        <section className="panel overview-alert-panel">
          <div className="toolbar">
            <div>
              <h2>最近告警</h2>
              <p className="muted">最近 60 分钟的平台异常。</p>
            </div>
            <Link href="/admin/monitor" className="text-icon-button">查看监控<ArrowRight size={14} /></Link>
          </div>
          {alerts.slice(0, 5).map((item) => (
            <div className="overview-alert-row" key={item.id}>
              <span className={alertTone(item.severity)}>{item.severity === "critical" ? "严重" : item.severity === "warning" ? "预警" : "提示"}</span>
              <span>
                <strong>{item.title}</strong>
                <small>{item.message}</small>
                <small>{item.tool_slug ? `工具 ${item.tool_slug}` : "平台"}{item.last_seen_at ? ` · ${new Date(item.last_seen_at).toLocaleString()}` : ""}</small>
              </span>
            </div>
          ))}
          {!loading && !alerts.length && <div className="state-box">最近 60 分钟没有告警。</div>}
        </section>
      </div>

      <section className="panel overview-tools-panel">
        <div className="toolbar">
          <div>
            <h2>工具运行状态</h2>
            <p className="muted">优先显示不可达、存在阻断或尚未完成接口治理的工具。</p>
          </div>
          <Link href="/admin/tools" className="icon-text-button"><Wrench size={15} />进入工具管理</Link>
        </div>
        <div className="overview-tool-list">
          {prioritizedTools.map((tool) => (
              <Link href={`/admin/tools/${tool.id}`} className="overview-tool-row" key={tool.id}>
                <span><strong>{tool.name}</strong><small>/gateway/{tool.slug}</small></span>
                <span className={tool.health?.status === "unknown" ? "status info" : tool.health?.reachable ? "status good" : "status danger"}>{tool.health?.status === "unknown" ? "未检测" : tool.health?.reachable ? "可达" : "不可达"}</span>
                <span><strong>{tool.published_count}</strong><small>已发布</small></span>
                <span><strong>{tool.blocked_count}</strong><small>阻断</small></span>
                <ArrowRight size={16} />
              </Link>
            ))}
          {!loading && !tools.length && <div className="state-box">尚未创建工具。</div>}
        </div>
      </section>

    </PortalShell>
  );
}
