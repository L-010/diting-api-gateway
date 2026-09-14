"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Clock3, Copy, Eye, FilterX, Gauge, HardDrive, KeyRound, RefreshCw, Save, Search, Settings, UserRound, X } from "lucide-react";
import { CallDetailDrawer } from "@/components/call-detail-drawer";
import { RemoteFileTable } from "@/components/remote-file-table";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { formatBytes, GatewayCall, GatewayCallPage, statusTone } from "@/lib/calls";
import { notifyToast } from "@/lib/toast";
import { copyToClipboard } from "@/lib/clipboard";
import { RemoteFilePage } from "@/lib/remote-files";

type AdminUser = {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  roles: string[];
  is_active: boolean;
  approval_status: string;
  must_change_password: boolean;
  registered_at: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  rejection_reason: string;
  disabled_at: string | null;
  disabled_by_user_id: string | null;
  disable_reason: string;
  can_enable: boolean;
  registration_note: string;
  rate_limit_per_minute: number;
  storage_quota_bytes: number;
  storage_used_bytes: number;
  api_key_count: number;
  active_api_key_count: number;
  last_api_activity_at: string | null;
  created_at: string;
  updated_at: string;
};

type AdminApiKey = {
  id: string;
  label: string;
  prefix: string;
  status: string;
  is_expired: boolean;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  disabled_at: string | null;
  disable_reason: string;
  can_enable: boolean;
  recent_failure_count: number;
  recent_rate_limited_count: number;
  last_error_code: string | null;
};

type UserSummary = {
  user: AdminUser;
  window_days: number;
  window_start: string;
  window_end: string;
  metrics: {
    total: number;
    success: number;
    failed: number;
    rate_limited: number;
    success_rate: number;
    avg_duration_ms: number;
    p95_duration_ms: number;
    request_bytes: number;
    response_bytes: number;
    last_called_at: string | null;
  };
  top_tools: Array<{ tool_slug: string; total: number; failed: number; avg_duration_ms: number }>;
  recent_failures: GatewayCall[];
};

type UserEvent = {
  id: string;
  action: string;
  actor_user_id: string;
  actor_username: string;
  target_type: string;
  target_id: string | null;
  request_id: string;
  detail: Record<string, unknown>;
  created_at: string;
};

type UserEventPage = { items: UserEvent[]; total: number; page: number; page_size: number; has_more: boolean };
type ProfileTab = "overview" | "settings" | "calls" | "files" | "keys" | "events";
type CallFilters = { requestId: string; toolSlug: string; result: string; method: string; keyId: string; start: string; end: string };

const PAGE_SIZE = 30;
const emptyCallFilters: CallFilters = { requestId: "", toolSlug: "", result: "", method: "", keyId: "", start: "", end: "" };
const actionLabels: Record<string, string> = {
  user_registered: "提交注册申请",
  user_approved: "管理员通过申请",
  user_rejected: "管理员拒绝申请",
  user_disabled: "管理员停用账号",
  user_enabled: "管理员重新启用账号",
  user_logged_in: "登录成功",
  user_login_failed: "登录失败",
  user_logged_out: "退出登录",
  password_changed: "修改密码",
  api_key_created: "创建 API Key",
  api_key_disabled: "用户禁用 API Key",
  api_key_enabled: "用户重新启用 API Key",
  admin_api_key_disabled: "管理员禁用 API Key",
  admin_api_key_enabled: "管理员重新启用 API Key",
  user_rate_limit_updated: "管理员修改用户限流",
};

function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString() : "-";
}

function userState(user: AdminUser) {
  if (user.approval_status === "pending") return { label: "待审批", className: "status warn" };
  if (user.approval_status === "rejected") return { label: "已拒绝", className: "status danger" };
  if (!user.is_active) return { label: "已停用", className: "status danger" };
  return { label: "已启用", className: "status good" };
}

function keyState(key: AdminApiKey) {
  if (key.is_expired) return { label: "已过期", className: "status warn" };
  if (key.status !== "active") return { label: "已禁用", className: "status danger" };
  return { label: "可用", className: "status good" };
}

function toIso(value: string) {
  return value ? new Date(value).toISOString() : "";
}

export default function AdminUserProfilePage() {
  const params = useParams<{ userId: string }>();
  const userId = params.userId;
  const [activeTab, setActiveTab] = useState<ProfileTab>("overview");
  const [windowDays, setWindowDays] = useState(30);
  const [summary, setSummary] = useState<UserSummary | null>(null);
  const [keys, setKeys] = useState<AdminApiKey[]>([]);
  const [calls, setCalls] = useState<GatewayCallPage>({ items: [], total: 0, page: 1, page_size: PAGE_SIZE, has_more: false });
  const [callFilters, setCallFilters] = useState<CallFilters>(emptyCallFilters);
  const [appliedCallFilters, setAppliedCallFilters] = useState<CallFilters>(emptyCallFilters);
  const [events, setEvents] = useState<UserEventPage>({ items: [], total: 0, page: 1, page_size: PAGE_SIZE, has_more: false });
  const [files, setFiles] = useState<RemoteFilePage>({ items: [], total: 0, page: 1, page_size: PAGE_SIZE, has_more: false, used_bytes: 0, quota_bytes: 0 });
  const [eventCategory, setEventCategory] = useState("all");
  const [selectedCall, setSelectedCall] = useState<GatewayCall | null>(null);
  const [loading, setLoading] = useState(true);
  const [callsLoading, setCallsLoading] = useState(false);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [detailLoadingId, setDetailLoadingId] = useState("");
  const [error, setError] = useState("");
  const [resettingPassword, setResettingPassword] = useState(false);
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [rateLimit, setRateLimit] = useState("0");
  const [rateLimitSaving, setRateLimitSaving] = useState(false);
  const [storageQuotaGiB, setStorageQuotaGiB] = useState("0");
  const [storageQuotaSaving, setStorageQuotaSaving] = useState(false);
  const [enablingKeys, setEnablingKeys] = useState<Set<string>>(new Set());
  const callSequence = useRef(0);
  const eventSequence = useRef(0);

  const loadSummary = useCallback(async (days: number, showToast = false) => {
    try {
      const next = await api<UserSummary>(`/api/admin/users/${userId}/summary?window_days=${days}`);
      setSummary(next);
      setRateLimit(String(next.user.rate_limit_per_minute));
      setStorageQuotaGiB(next.user.storage_quota_bytes ? String(next.user.storage_quota_bytes / 1_073_741_824) : "0");
      if (showToast) notifyToast({ type: "success", message: `已刷新 ${next.user.username} 的用户档案` });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      if (showToast) notifyToast({ type: "error", message });
    }
  }, [userId]);

  const loadKeys = useCallback(async () => {
    setKeys(await api<AdminApiKey[]>(`/api/admin/users/${userId}/api-keys`));
  }, [userId]);

  const loadCalls = useCallback(async (page: number, filters: CallFilters, showToast = false) => {
    const sequence = ++callSequence.current;
    setCallsLoading(true);
    const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (filters.requestId.trim()) query.set("request_id", filters.requestId.trim());
    if (filters.toolSlug.trim()) query.set("tool_slug", filters.toolSlug.trim());
    if (filters.result) query.set("result", filters.result);
    if (filters.method) query.set("method", filters.method);
    if (filters.keyId) query.set("api_key_id", filters.keyId);
    if (filters.start) query.set("start", toIso(filters.start));
    if (filters.end) query.set("end", toIso(filters.end));
    try {
      const next = await api<GatewayCallPage>(`/api/admin/users/${userId}/calls?${query}`);
      if (sequence !== callSequence.current) return;
      setCalls(next);
      setAppliedCallFilters(filters);
      if (showToast) notifyToast({ type: "success", message: `调用记录查询完成：共 ${next.total} 条` });
    } catch (cause) {
      if (sequence !== callSequence.current) return;
      const message = errorMessage(cause);
      setError(message);
      if (showToast) notifyToast({ type: "error", message });
    } finally {
      if (sequence === callSequence.current) setCallsLoading(false);
    }
  }, [userId]);

  const loadEvents = useCallback(async (page: number, category: string) => {
    const sequence = ++eventSequence.current;
    setEventsLoading(true);
    try {
      const next = await api<UserEventPage>(`/api/admin/users/${userId}/events?page=${page}&page_size=${PAGE_SIZE}&category=${category}`);
      if (sequence === eventSequence.current) setEvents(next);
    } catch (cause) {
      if (sequence === eventSequence.current) setError(errorMessage(cause));
    } finally {
      if (sequence === eventSequence.current) setEventsLoading(false);
    }
  }, [userId]);

  const loadFiles = useCallback(async (page = 1) => {
    try { setFiles(await api<RemoteFilePage>(`/api/admin/users/${userId}/files?page=${page}&page_size=${PAGE_SIZE}`)); }
    catch (cause) { setError(errorMessage(cause)); }
  }, [userId]);

  useEffect(() => {
    const queryTab = new URLSearchParams(window.location.search).get("tab") as ProfileTab | null;
    if (queryTab && ["overview", "settings", "calls", "files", "keys", "events"].includes(queryTab)) setActiveTab(queryTab);
    Promise.all([loadSummary(30), loadKeys(), loadCalls(1, emptyCallFilters), loadEvents(1, "all"), loadFiles(1)])
      .catch((cause) => setError(errorMessage(cause)))
      .finally(() => setLoading(false));
  }, [loadCalls, loadEvents, loadFiles, loadKeys, loadSummary]);

  function chooseTab(tab: ProfileTab) {
    setActiveTab(tab);
    window.history.replaceState(null, "", `/admin/users/${userId}?tab=${tab}`);
  }

  function chooseWindow(days: number) {
    setWindowDays(days);
    void loadSummary(days, true);
  }

  function submitCalls(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadCalls(1, callFilters, true);
  }

  function resetCalls() {
    setCallFilters(emptyCallFilters);
    void loadCalls(1, emptyCallFilters, true);
  }

  function filterByKey(key: AdminApiKey) {
    const next = { ...emptyCallFilters, keyId: key.id };
    setCallFilters(next);
    chooseTab("calls");
    void loadCalls(1, next, true);
  }

  async function openCall(requestId: string) {
    setDetailLoadingId(requestId);
    try {
      setSelectedCall(await api<GatewayCall>(`/api/admin/calls/${requestId}`));
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setDetailLoadingId("");
    }
  }

  function chooseEventCategory(category: string) {
    setEventCategory(category);
    void loadEvents(1, category);
  }

  async function forceResetPassword() {
    if (resettingPassword) return;
    const reason = window.prompt("请输入强制重置原因（至少 2 个字符），该原因会写入审计日志：");
    if (reason === null) return;
    if (reason.trim().length < 2) { notifyToast({ type: "error", message: "重置原因至少 2 个字符" }); return; }
    setResettingPassword(true);
    try {
      const result = await api<{ temporary_password: string }>(`/api/admin/users/${userId}/reset-password`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
      setTemporaryPassword(result.temporary_password);
      await loadSummary(windowDays);
      notifyToast({ type: "success", message: "临时密码已生成，现有登录态已失效" });
    } catch (cause) { notifyToast({ type: "error", message: errorMessage(cause) }); }
    finally { setResettingPassword(false); }
  }

  async function enableKey(key: AdminApiKey) {
    if (enablingKeys.has(key.id)) return;
    const reason = window.prompt(
      `确认重新启用 API Key ${key.prefix}...？\n\n重新启用后，原完整密钥会立即恢复可用。请输入操作原因（至少 2 个字符），该原因会写入审计日志：`,
      "已确认该 API Key 可以恢复使用",
    );
    if (reason === null) return;
    if (reason.trim().length < 2) {
      notifyToast({ type: "error", message: "重新启用原因至少 2 个字符" });
      return;
    }
    setEnablingKeys((current) => new Set(current).add(key.id));
    setError("");
    try {
      await api<AdminApiKey>(`/api/admin/api-keys/${encodeURIComponent(key.id)}/enable`, {
        method: "PATCH",
        body: JSON.stringify({ reason: reason.trim() }),
      });
      await Promise.all([loadKeys(), loadSummary(windowDays), loadEvents(events.page, eventCategory)]);
      notifyToast({ type: "success", message: `Key ${key.prefix}... 已重新启用` });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setEnablingKeys((current) => { const next = new Set(current); next.delete(key.id); return next; });
    }
  }

  async function saveRateLimit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (rateLimitSaving) return;
    const value = Number(rateLimit);
    if (!Number.isInteger(value) || value < 0 || value > 10_000) {
      notifyToast({ type: "error", message: "每分钟请求上限必须是 0 到 10000 的整数" });
      return;
    }
    setRateLimitSaving(true);
    try {
      const updated = await api<AdminUser>(`/api/admin/users/${userId}/rate-limit`, {
        method: "PATCH",
        body: JSON.stringify({ rate_limit_per_minute: value }),
      });
      setSummary((current) => current ? { ...current, user: updated } : current);
      setRateLimit(String(updated.rate_limit_per_minute));
      notifyToast({ type: "success", message: `${updated.username} 的用户级限流已更新` });
    } catch (cause) {
      notifyToast({ type: "error", message: "用户限流保存失败", details: errorMessage(cause) });
    } finally {
      setRateLimitSaving(false);
    }
  }

  async function saveStorageQuota(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (storageQuotaSaving) return;
    const gib = Number(storageQuotaGiB);
    if (!Number.isFinite(gib) || gib < 0 || gib > 102_400) { notifyToast({ type: "error", message: "容量必须是 0 到 102400 GiB 的数字" }); return; }
    const bytes = Math.round(gib * 1_073_741_824);
    setStorageQuotaSaving(true);
    try {
      const updated = await api<AdminUser>(`/api/admin/users/${userId}/storage-quota`, { method: "PATCH", body: JSON.stringify({ storage_quota_bytes: bytes }) });
      setSummary(current => current ? { ...current, user: updated } : current);
      setStorageQuotaGiB(updated.storage_quota_bytes ? String(updated.storage_quota_bytes / 1_073_741_824) : "0");
      await loadFiles(files.page);
      notifyToast({ type: "success", message: `${updated.username} 的文件配额已更新` });
    } catch (cause) { notifyToast({ type: "error", message: "文件配额保存失败", details: errorMessage(cause) }); }
    finally { setStorageQuotaSaving(false); }
  }

  if (loading && !summary) {
    return <PortalShell admin title="用户档案"><div className="state-box">正在加载用户完整档案...</div></PortalShell>;
  }

  if (!summary) {
    return <PortalShell admin title="用户档案"><div className="state-box error">{error || "用户档案不可用"}</div><Link href="/admin/users" className="button secondary">返回用户管理</Link></PortalShell>;
  }

  const user = summary.user;
  const state = userState(user);
  const callPageCount = Math.max(1, Math.ceil(calls.total / PAGE_SIZE));
  const eventPageCount = Math.max(1, Math.ceil(events.total / PAGE_SIZE));

  return (
    <PortalShell admin title="用户档案">
      <div className="profile-heading">
        <div className="profile-identity">
          <span className="user-avatar large" aria-hidden="true">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</span>
          <div><span className={state.className}>{state.label}</span><h2>{user.display_name || user.username}</h2><p>@{user.username} · {user.roles.includes("admin") ? "管理员" : "普通用户"}</p></div>
        </div>
        <div className="toolbar-actions">
          <Link href="/admin/users" className="button secondary"><ArrowLeft size={15} />返回列表</Link>
          {!user.roles.includes("admin") && user.approval_status === "approved" && <button className="button secondary" type="button" disabled={resettingPassword} onClick={() => void forceResetPassword()}><KeyRound size={15} />{resettingPassword ? "重置中" : "强制重置密码"}</button>}
          <Link href={`/admin/users?focus=${encodeURIComponent(user.id)}`} className="button"><Settings size={15} />管理账号</Link>
        </div>
      </div>

      {error && <div className="state-box error" role="alert">{error}</div>}
      {temporaryPassword && <div className="dialog-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="temporary-password-title"><div className="toolbar"><h2 id="temporary-password-title">一次性临时密码</h2><button className="icon-button" aria-label="关闭并清除临时密码" onClick={() => setTemporaryPassword("")}><X size={17}/></button></div><p className="notice">只在此处显示一次。用户登录后必须立即修改，关闭后管理员无法恢复。</p><div className="key-secret">{temporaryPassword}</div><div className="dialog-actions" style={{ marginTop: 16 }}><button className="button secondary" onClick={() => void copyToClipboard(temporaryPassword, "临时密码已复制")}><Copy size={15}/>复制</button><button className="button" onClick={() => setTemporaryPassword("")}>我已保存，关闭</button></div></section></div>}

      <nav className="profile-tabs" aria-label="用户档案视图">
        {([ ["overview", "概览"], ["settings", "限制设置"], ["calls", `调用记录 ${calls.total}`], ["files", `任务与文件 ${files.total}`], ["keys", `API Key ${keys.length}`], ["events", `账号与事件 ${events.total}`] ] as Array<[ProfileTab, string]>).map(([value, label]) => (
          <button key={value} type="button" className={activeTab === value ? "active" : ""} aria-current={activeTab === value ? "page" : undefined} onClick={() => chooseTab(value)}>{label}</button>
        ))}
      </nav>

      {activeTab === "overview" && (
        <div className="profile-view">
          <div className="profile-range-row">
            <span>统计范围</span>
            <div className="segmented-control" aria-label="统计时间范围">{[1, 7, 30, 90].map((days) => <button key={days} type="button" className={windowDays === days ? "active" : ""} onClick={() => chooseWindow(days)}>{days === 1 ? "24 小时" : `${days} 天`}</button>)}</div>
            <button className="icon-button" type="button" aria-label="刷新用户档案" onClick={() => void loadSummary(windowDays, true)}><RefreshCw size={16} /></button>
          </div>
          <div className="metrics profile-metrics">
            <Metric label="调用量" value={String(summary.metrics.total)} caption={`${summary.metrics.success} 成功 / ${summary.metrics.failed} 失败`} />
            <Metric label="成功率" value={`${(summary.metrics.success_rate * 100).toFixed(1)}%`} caption={`限流 ${summary.metrics.rate_limited} 次`} tone="green" />
            <Metric label="p95 延迟" value={`${summary.metrics.p95_duration_ms} ms`} caption={`平均 ${summary.metrics.avg_duration_ms} ms`} tone="purple" />
            <Metric label="总流量" value={formatBytes(summary.metrics.request_bytes + summary.metrics.response_bytes)} caption={`最后调用 ${formatDate(summary.metrics.last_called_at)}`} tone="amber" />
          </div>
          <div className="profile-overview-grid">
            <section className="panel profile-account-panel">
              <h2><UserRound size={18} /> 账号信息</h2>
              <dl className="profile-facts">
                <div><dt>邮箱</dt><dd>{user.email || "未填写"}</dd></div>
                <div><dt>首次登录改密</dt><dd>{user.must_change_password ? "需要" : "已完成或不需要"}</dd></div>
                <div><dt>注册时间</dt><dd>{formatDate(user.registered_at || user.created_at)}</dd></div>
                <div><dt>审批时间</dt><dd>{formatDate(user.approved_at)}</dd></div>
                <div><dt>API Key</dt><dd>{user.active_api_key_count} 个可用 / 共 {user.api_key_count} 个</dd></div>
                <div><dt>最后 API 活动</dt><dd>{formatDate(user.last_api_activity_at)}</dd></div>
                <div><dt>用户级限流</dt><dd>{user.rate_limit_per_minute === 0 ? "无限制" : `${user.rate_limit_per_minute} 次/分钟`}</dd></div>
              </dl>
              <div className="profile-note"><span>用途说明</span><p>{user.registration_note || "未填写用途说明。"}</p></div>
              {(user.rejection_reason || user.disable_reason) && <div className="profile-note danger"><span>当前限制原因</span><p>{user.disable_reason || user.rejection_reason}</p></div>}
            </section>
            <section className="panel">
              <h2>常用工具</h2>
              {summary.top_tools.map((item) => <div className="list-row" key={item.tool_slug}><span><strong>{item.tool_slug}</strong><small>失败 {item.failed} · 平均 {item.avg_duration_ms} ms</small></span><strong>{item.total}</strong></div>)}
              {!summary.top_tools.length && <div className="state-box">统计范围内没有调用。</div>}
            </section>
          </div>
          <section className="panel profile-recent-panel">
            <div className="toolbar"><div><h2>最近失败</h2><p className="muted">用于快速判断是访问控制、路由还是上游问题。</p></div><button className="button secondary compact" type="button" onClick={() => { const next = { ...emptyCallFilters, result: "failed" }; setCallFilters(next); chooseTab("calls"); void loadCalls(1, next); }}>查看全部失败</button></div>
            <div className="table-wrap"><table className="data-table profile-call-table"><thead><tr><th>时间</th><th>工具与接口</th><th>结果</th><th>耗时</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>
              {summary.recent_failures.map((item) => <tr key={item.request_id}><td>{formatDate(item.created_at)}</td><td><strong>{item.endpoint_summary || item.operation_id || item.path}</strong><code className="call-path">/gateway/{item.tool_slug}{item.path}</code></td><td><span className={statusTone(item.status_code)}>{item.status_code}</span><span className="call-result-text">{item.error_code || "调用失败"}</span></td><td>{item.duration_ms} ms</td><td><button className="icon-button" type="button" aria-label={`查看调用 ${item.request_id}`} onClick={() => void openCall(item.request_id)}><Eye size={15} /></button></td></tr>)}
              {!summary.recent_failures.length && <tr><td colSpan={5}><div className="state-box">统计范围内没有失败请求。</div></td></tr>}
            </tbody></table></div>
          </section>
        </div>
      )}

      {activeTab === "calls" && (
        <div className="profile-view">
          <section className="panel profile-call-filter-panel">
            <form className="profile-call-filter-grid" onSubmit={submitCalls}>
              <label className="form-field">request_id<input value={callFilters.requestId} onChange={(event) => setCallFilters((current) => ({ ...current, requestId: event.target.value }))} placeholder="精确定位一次调用" /></label>
              <label className="form-field">工具 slug<input value={callFilters.toolSlug} onChange={(event) => setCallFilters((current) => ({ ...current, toolSlug: event.target.value }))} placeholder="例如 tomodd" /></label>
              <label className="form-field">结果<select value={callFilters.result} onChange={(event) => setCallFilters((current) => ({ ...current, result: event.target.value }))}><option value="">全部结果</option><option value="success">成功</option><option value="failed">失败</option><option value="rate_limited">限流</option></select></label>
              <label className="form-field">方法<select value={callFilters.method} onChange={(event) => setCallFilters((current) => ({ ...current, method: event.target.value }))}><option value="">全部方法</option>{["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"].map((item) => <option key={item}>{item}</option>)}</select></label>
              <label className="form-field">API Key<select value={callFilters.keyId} onChange={(event) => setCallFilters((current) => ({ ...current, keyId: event.target.value }))}><option value="">全部 Key</option>{keys.map((key) => <option key={key.id} value={key.id}>{key.label} · {key.prefix}...</option>)}</select></label>
              <label className="form-field">开始时间<input type="datetime-local" value={callFilters.start} onChange={(event) => setCallFilters((current) => ({ ...current, start: event.target.value }))} /></label>
              <label className="form-field">结束时间<input type="datetime-local" value={callFilters.end} onChange={(event) => setCallFilters((current) => ({ ...current, end: event.target.value }))} /></label>
              <div className="profile-filter-actions"><button className="button secondary" type="button" disabled={callsLoading} onClick={resetCalls}><FilterX size={15} />清空</button><button className="button" type="submit" disabled={callsLoading}><Search size={15} />{callsLoading ? "查询中" : "查询"}</button></div>
            </form>
          </section>
          <section className="panel profile-call-list-panel">
            <div className="toolbar"><div><h2>Gateway 请求</h2><p className="muted">共 {calls.total} 条匹配记录，按时间倒序排列。</p></div><button className="icon-button" type="button" aria-label="刷新调用记录" disabled={callsLoading} onClick={() => void loadCalls(calls.page, appliedCallFilters, true)}><RefreshCw size={16} /></button></div>
            <div className="table-wrap"><table className="data-table profile-call-table"><thead><tr><th>时间 / request_id</th><th>Key</th><th>工具与接口</th><th>结果</th><th>耗时 / 流量</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>
              {calls.items.map((item) => <tr key={item.request_id}><td><strong>{formatDate(item.created_at)}</strong><code className="call-request-id">{item.request_id}</code></td><td>{item.api_key_prefix ? <code>{item.api_key_prefix}...</code> : "未识别"}</td><td><strong>{item.endpoint_summary || item.operation_id || item.path}</strong><code className="call-path">{item.method} /gateway/{item.tool_slug}{item.path}</code></td><td><span className={statusTone(item.status_code)}>{item.status_code}</span><span className="call-result-text">{item.error_code || "调用成功"}</span><span className="muted">上游 {item.upstream_status_code ?? "-"}</span></td><td>{item.duration_ms} ms<br /><span className="muted">{formatBytes(item.request_bytes)} / {formatBytes(item.response_bytes)}</span></td><td><button className="icon-button" type="button" aria-label={`查看调用 ${item.request_id}`} disabled={detailLoadingId === item.request_id} onClick={() => void openCall(item.request_id)}><Eye size={15} /></button></td></tr>)}
              {!calls.items.length && !callsLoading && <tr><td colSpan={6}><div className="state-box">没有匹配的调用记录。</div></td></tr>}
            </tbody></table></div>
            <div className="audit-pagination"><span className="muted">第 {calls.page} / {callPageCount} 页，每页 {PAGE_SIZE} 条</span><div className="toolbar-actions"><button className="button secondary compact" type="button" disabled={callsLoading || calls.page <= 1} onClick={() => void loadCalls(calls.page - 1, appliedCallFilters)}><ChevronLeft size={14} />上一页</button><button className="button secondary compact" type="button" disabled={callsLoading || !calls.has_more} onClick={() => void loadCalls(calls.page + 1, appliedCallFilters)}>下一页<ChevronRight size={14} /></button></div></div>
          </section>
        </div>
      )}

      {activeTab === "settings" && (
        <div className="profile-view">
          <form className="panel user-limit-panel" onSubmit={saveRateLimit}>
            <div className="settings-section-heading">
              <div><h2><Gauge size={19} />用户级请求限制</h2><p>该限制按用户聚合全部 API Key 和全部工具调用，并与工具自身限流同时执行。</p></div>
              <span className={user.rate_limit_per_minute === 0 ? "status info" : "status warn"}>{user.rate_limit_per_minute === 0 ? "当前无限制" : `${user.rate_limit_per_minute} 次/分钟`}</span>
            </div>
            <div className="user-limit-form">
              <label className="form-field">每分钟请求上限<input value={rateLimit} onChange={(event) => setRateLimit(event.target.value)} type="number" min={0} max={10000} step={1} inputMode="numeric" required /><small>填写 0 表示不增加用户级限制；不会关闭工具自身的保护策略。</small></label>
              <div className="notice"><strong>生效范围</strong><p>保存后立即生效。当前分钟已经产生的请求数会继续计入同一窗口，下一自然分钟自动重新计数。</p></div>
            </div>
            <div className="settings-actions"><span>平台默认值只用于新用户，此处修改仅影响 @{user.username}。</span><button className="button" type="submit" disabled={rateLimitSaving}><Save size={15} />{rateLimitSaving ? "保存中" : "保存用户限制"}</button></div>
          </form>
          <form className="panel user-limit-panel" onSubmit={saveStorageQuota}>
            <div className="settings-section-heading"><div><h2><HardDrive size={19}/>用户文件配额</h2><p>按未删除文件的确认大小统计。超过配额后仍允许任务完成、下载和删除，但阻止新的上传与产文件任务。</p></div><span className="status info">已用 {formatBytes(user.storage_used_bytes)}</span></div>
            <div className="user-limit-form"><label className="form-field">容量覆盖（GiB）<input value={storageQuotaGiB} onChange={event=>setStorageQuotaGiB(event.target.value)} type="number" min={0} max={102400} step={1} inputMode="decimal" required/><small>填写 0 表示继承平台默认容量；不会将用户容量设置为零。</small></label><div className="notice"><strong>保护边界</strong><p>降低配额不会删除已有文件，也不会中止运行中的任务。实际过期时间仍取平台策略与工具服务器过期时间中的较早值。</p></div></div>
            <div className="settings-actions"><span>当前有效容量可在“任务与文件”标签查看。</span><button className="button" type="submit" disabled={storageQuotaSaving}><Save size={15}/>{storageQuotaSaving?"保存中":"保存文件配额"}</button></div>
          </form>
        </div>
      )}

      {activeTab === "files" && (
        <div className="profile-view"><section className="panel"><div className="toolbar"><div><h2>用户任务与文件</h2><p className="muted">已用 {formatBytes(files.used_bytes)} / {formatBytes(files.quota_bytes)}；管理员下载会自动审计。</p></div><div className="toolbar-actions"><Link className="button secondary compact" href={`/admin/files?view=tasks&user_id=${encodeURIComponent(user.id)}`}>查看用户任务</Link><button className="icon-button" aria-label="刷新用户文件" onClick={()=>void loadFiles(files.page)}><RefreshCw size={16}/></button></div></div><RemoteFileTable files={files.items} admin onChanged={()=>void loadFiles(files.page)}/><div className="audit-pagination"><span>第 {files.page} 页，共 {files.total} 条</span><div className="toolbar-actions"><button className="button secondary compact" disabled={files.page<=1} onClick={()=>void loadFiles(files.page-1)}><ChevronLeft size={14}/>上一页</button><button className="button secondary compact" disabled={!files.has_more} onClick={()=>void loadFiles(files.page+1)}>下一页<ChevronRight size={14}/></button></div></div></section></div>
      )}

      {activeTab === "keys" && (
        <div className="profile-view"><section className="panel profile-call-list-panel"><div className="toolbar"><div><h2>API Key</h2><p className="muted">Key 不展示完整密钥；历史调用会继续保留。</p></div><button className="icon-button" type="button" aria-label="刷新 API Key" onClick={() => void loadKeys().then(() => notifyToast({ type: "success", message: "API Key 已刷新" }))}><RefreshCw size={16} /></button></div><div className="table-wrap"><table className="data-table profile-key-table"><thead><tr><th>名称 / 前缀</th><th>状态</th><th>最近使用</th><th>近期信号</th><th>创建 / 过期</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>
          {keys.map((key) => { const keyStatus = keyState(key); return <tr key={key.id}><td><strong>{key.label}</strong><code className="call-request-id">{key.prefix}...</code></td><td><span className={keyStatus.className}>{keyStatus.label}</span>{key.disable_reason && <span className="cell-note">{key.disable_reason}</span>}</td><td>{formatDate(key.last_used_at)}</td><td>失败 {key.recent_failure_count}<br /><span className="muted">限流 {key.recent_rate_limited_count} · {key.last_error_code || "无近期错误"}</span></td><td>{formatDate(key.created_at)}<br /><span className="muted">过期 {formatDate(key.expires_at)}</span></td><td><div className="toolbar-actions"><button className="button secondary compact" type="button" onClick={() => filterByKey(key)}><Search size={14} />查看调用</button>{key.status === "disabled" && !key.is_expired && key.can_enable && <button className="button compact" type="button" disabled={enablingKeys.has(key.id)} onClick={() => void enableKey(key)}><RefreshCw size={14} className={enablingKeys.has(key.id) ? "spin" : undefined} />{enablingKeys.has(key.id) ? "启用中" : "重新启用"}</button>}</div></td></tr>; })}
          {!keys.length && <tr><td colSpan={6}><div className="state-box">该用户尚未创建 API Key。</div></td></tr>}
        </tbody></table></div></section></div>
      )}

      {activeTab === "events" && (
        <div className="profile-view"><section className="panel profile-event-panel"><div className="toolbar"><div><h2>账号与事件</h2><p className="muted">账号状态、登录、改密和 Key 生命周期记录。</p></div><div className="segmented-control" aria-label="事件类型">{[["all", "全部"], ["account", "账号"], ["key", "Key"]].map(([value, label]) => <button key={value} type="button" className={eventCategory === value ? "active" : ""} onClick={() => chooseEventCategory(value)}>{label}</button>)}</div></div>
          <div className="profile-event-list">{events.items.map((item) => <article className="profile-event-row" key={item.id}><span className="event-marker"><Clock3 size={15} /></span><div><div className="event-heading"><strong>{actionLabels[item.action] || item.action}</strong><time>{formatDate(item.created_at)}</time></div><p>操作者：{item.actor_username} · request_id：<code>{item.request_id}</code></p>{typeof item.detail.reason === "string" && item.detail.reason && <p className="event-reason">原因：{item.detail.reason}</p>}{typeof item.detail.key_prefix === "string" && <p>Key：<code>{item.detail.key_prefix}...</code></p>}</div></article>)}{!events.items.length && !eventsLoading && <div className="state-box">暂无匹配事件。</div>}</div>
          <div className="audit-pagination"><span className="muted">第 {events.page} / {eventPageCount} 页，共 {events.total} 条</span><div className="toolbar-actions"><button className="button secondary compact" type="button" disabled={eventsLoading || events.page <= 1} onClick={() => void loadEvents(events.page - 1, eventCategory)}><ChevronLeft size={14} />上一页</button><button className="button secondary compact" type="button" disabled={eventsLoading || !events.has_more} onClick={() => void loadEvents(events.page + 1, eventCategory)}>下一页<ChevronRight size={14} /></button></div></div>
        </section></div>
      )}

      <CallDetailDrawer call={selectedCall} admin onClose={() => setSelectedCall(null)} />
    </PortalShell>
  );
}
