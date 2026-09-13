"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  ChevronLeft,
  ChevronRight,
  ClipboardCopy,
  ExternalLink,
  Eye,
  FilterX,
  RefreshCw,
  Search,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { copyToClipboard } from "@/lib/clipboard";
import { notifyToast } from "@/lib/toast";

type AuditEvent = {
  id: string;
  action: string;
  target_type: string;
  target_id: string | null;
  admin_user_id: string;
  admin_username: string;
  request_id: string;
  created_at: string;
  detail: Record<string, unknown>;
};

type AuditResponse = {
  items: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
  available_actions: string[];
  available_target_types: string[];
};

type AuditFilters = {
  category: string;
  action: string;
  targetType: string;
  targetId: string;
  adminUsername: string;
  requestId: string;
  start: string;
  end: string;
};

const PAGE_SIZE = 30;

const emptyFilters: AuditFilters = {
  category: "",
  action: "",
  targetType: "",
  targetId: "",
  adminUsername: "",
  requestId: "",
  start: "",
  end: "",
};

const categoryLabels: Record<string, string> = {
  accounts: "账号与访问",
  keys: "API Key",
  tools: "工具配置",
  interfaces: "接口与发布",
  settings: "平台设置",
  files: "任务与文件",
};

const actionLabels: Record<string, string> = {
  user_registered: "提交注册申请",
  user_approved: "审批通过用户",
  user_rejected: "拒绝用户申请",
  user_disabled: "停用用户",
  user_enabled: "启用用户",
  user_import: "批量导入用户",
  user_logged_in: "用户登录成功",
  user_login_failed: "用户登录失败",
  user_logged_out: "用户退出登录",
  password_changed: "用户修改密码",
  api_key_created: "创建 API Key",
  api_key_disabled: "用户禁用 API Key",
  admin_api_key_disabled: "管理员禁用 API Key",
  tool_created: "创建工具",
  tool_updated: "更新工具配置",
  tool_health_tested: "执行工具健康检查",
  tool_release_validated: "执行发布校验",
  tool_token_replaced: "替换上游凭据",
  tool_configuration_tested: "测试候选工具配置",
  tool_configuration_updated: "更新工具运行配置",
  tool_configuration_rolled_back: "回滚工具运行配置",
  openapi_parsed: "解析 OpenAPI 文档",
  openapi_uploaded: "上传 OpenAPI 文档",
  openapi_imported: "导入 OpenAPI 文档",
  openapi_sync_diff_created: "生成接口同步差异",
  openapi_import_confirmed: "确认导入批次",
  openapi_diff_decision: "处理接口差异",
  openapi_diff_bulk_decision: "批量处理接口差异",
  endpoint_updated: "更新接口治理配置",
  endpoint_excluded: "排除接口",
  endpoint_blocked: "阻断接口",
  route_published: "发布路由",
  route_disabled: "停用路由",
  routes_bulk_published: "批量发布路由",
  platform_email_settings_updated: "更新平台邮箱配置",
  platform_gateway_settings_updated: "更新统一网关地址",
  platform_user_defaults_updated: "更新新用户默认设置",
  smtp_connection_tested: "测试 SMTP 连接",
  smtp_test_email_queued: "发送 SMTP 测试邮件",
  user_rate_limit_updated: "更新用户限流",
  admin_file_download_authorized: "创建管理员下载授权",
  admin_file_download_completed: "管理员文件下载完成",
  admin_file_download_failed: "管理员文件下载失败",
  admin_file_download_interrupted: "管理员文件下载中断",
  remote_file_quarantined: "隔离远程文件",
  remote_file_sync_retried: "重试文件同步",
  task_file_sync_retried: "重试任务文件同步",
  tool_storage_endpoint_removed: "移除历史存储端点",
  tool_integration_route_updated: "更新工具内部能力路由",
  tool_file_access_updated: "更新工具文件访问策略",
};

const targetLabels: Record<string, string> = {
  user: "用户",
  api_key: "API Key",
  user_import_batch: "用户导入批次",
  tool: "工具",
  tool_import_batch: "接口导入批次",
  api_endpoint: "接口",
  openapi_diff_item: "接口差异",
  platform_settings: "平台设置",
  email_outbox: "邮件队列",
  remote_file: "远程文件",
  external_resource: "平台任务",
  tool_storage_endpoint: "存储端点",
};

function actionCategory(action: string) {
  if (["admin_file_download_authorized", "admin_file_download_completed", "admin_file_download_failed", "admin_file_download_interrupted", "remote_file_quarantined", "remote_file_sync_retried", "task_file_sync_retried", "tool_storage_endpoint_removed", "tool_integration_route_updated", "tool_file_access_updated"].includes(action)) return "files";
  if (["platform_email_settings_updated", "platform_gateway_settings_updated", "platform_user_defaults_updated", "smtp_connection_tested", "smtp_test_email_queued", "user_rate_limit_updated"].includes(action)) return "settings";
  if (["api_key_created", "api_key_disabled", "admin_api_key_disabled"].includes(action)) return "keys";
  if (action.startsWith("user_") || action === "password_changed") return "accounts";
  if (action.startsWith("tool_")) return "tools";
  return "interfaces";
}

function actionTone(action: string) {
  if (["user_rejected", "user_disabled", "api_key_disabled", "admin_api_key_disabled", "endpoint_blocked", "route_disabled", "remote_file_quarantined", "admin_file_download_failed", "admin_file_download_interrupted"].includes(action)) return "status warn";
  if (["user_approved", "user_enabled", "route_published", "routes_bulk_published", "openapi_import_confirmed", "admin_file_download_completed"].includes(action)) return "status good";
  return "status info";
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function detailLabel(key: string) {
  const labels: Record<string, string> = {
    username: "用户名",
    reason: "操作原因",
    tool_slug: "工具 slug",
    gateway_path: "Gateway 路径",
    method: "请求方法",
    batch_id: "导入批次 ID",
    key_prefix: "Key 前缀",
    endpoint_id: "接口 ID",
    operation_count: "接口数量",
    published_count: "发布数量",
    decision: "治理决策",
    risk_level: "风险等级",
    status: "状态",
    version: "版本",
    sha256: "文档摘要",
  };
  return labels[key] || key;
}

function toIso(localValue: string) {
  return localValue ? new Date(localValue).toISOString() : "";
}

function toLocalInput(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function targetLink(item: AuditEvent): string | null {
  if (item.target_type === "user" && item.target_id) return `/admin/users/${encodeURIComponent(item.target_id)}?tab=events`;
  if (item.target_type === "tool" && item.target_id) return `/admin/tools/${encodeURIComponent(item.target_id)}`;
  const toolId = typeof item.detail.tool_id === "string" ? item.detail.tool_id : "";
  if (item.target_type === "api_endpoint" && item.target_id && toolId) {
    return `/admin/tools/${encodeURIComponent(toolId)}/endpoints/${encodeURIComponent(item.target_id)}`;
  }
  if (item.target_type === "api_key" && typeof item.detail.user_id === "string") {
    return `/admin/users/${encodeURIComponent(item.detail.user_id)}?tab=keys`;
  }
  if (item.target_type === "api_key" && typeof item.detail.key_prefix === "string") {
    return `/admin/users?key_prefix=${encodeURIComponent(item.detail.key_prefix)}`;
  }
  return null;
}

export default function AdminAuditPage() {
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [filters, setFilters] = useState<AuditFilters>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<AuditFilters>(emptyFilters);
  const [availableActions, setAvailableActions] = useState<string[]>([]);
  const [availableTargetTypes, setAvailableTargetTypes] = useState<string[]>([]);
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const requestSequence = useRef(0);
  const auditDrawerRef = useRef<HTMLElement>(null);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const activeFilterCount = Object.values(appliedFilters).filter(Boolean).length;
  const filteredActions = useMemo(
    () => availableActions.filter((item) => !filters.category || actionCategory(item) === filters.category),
    [availableActions, filters.category],
  );

  async function load(nextPage: number, nextFilters: AuditFilters, showToast = false) {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ page: String(nextPage), page_size: String(PAGE_SIZE) });
    if (nextFilters.category) params.set("category", nextFilters.category);
    if (nextFilters.action) params.set("action", nextFilters.action);
    if (nextFilters.targetType) params.set("target_type", nextFilters.targetType);
    if (nextFilters.targetId.trim()) params.set("target_id", nextFilters.targetId.trim());
    if (nextFilters.adminUsername.trim()) params.set("admin_username", nextFilters.adminUsername.trim());
    if (nextFilters.requestId.trim()) params.set("request_id", nextFilters.requestId.trim());
    if (nextFilters.start) params.set("start", toIso(nextFilters.start));
    if (nextFilters.end) params.set("end", toIso(nextFilters.end));
    window.history.replaceState(null, "", `/admin/audit?${params.toString()}`);
    try {
      const response = await api<AuditResponse>(`/api/admin/audit?${params.toString()}`);
      if (sequence !== requestSequence.current) return;
      setItems(response.items);
      setTotal(response.total);
      setPage(response.page);
      setHasMore(response.has_more);
      setAvailableActions(response.available_actions);
      setAvailableTargetTypes(response.available_target_types);
      setAppliedFilters(nextFilters);
      if (selected && !response.items.some((item) => item.id === selected.id)) setSelected(null);
      if (showToast) notifyToast({ type: "success", message: `审计查询完成：共 ${response.total} 条记录` });
    } catch (cause) {
      if (sequence !== requestSequence.current) return;
      const message = errorMessage(cause);
      setError(message);
      if (showToast) notifyToast({ type: "error", message });
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (filters.start && filters.end && new Date(filters.start) > new Date(filters.end)) {
      const message = "开始时间不能晚于结束时间";
      setError(message);
      notifyToast({ type: "error", message });
      return;
    }
    void load(1, filters, true);
  }

  function resetFilters() {
    setFilters(emptyFilters);
    void load(1, emptyFilters, true);
  }

  function changePage(nextPage: number) {
    setSelected(null);
    void load(nextPage, appliedFilters);
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialFilters: AuditFilters = {
      category: params.get("category") || "",
      action: params.get("action") || "",
      targetType: params.get("target_type") || "",
      targetId: params.get("target_id") || "",
      adminUsername: params.get("admin_username") || "",
      requestId: params.get("request_id") || "",
      start: toLocalInput(params.get("start")),
      end: toLocalInput(params.get("end")),
    };
    const initialPage = Math.max(1, Number(params.get("page") || 1));
    setFilters(initialFilters);
    void load(initialPage, initialFilters);
  }, []);

  useEffect(() => {
    if (!selected) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const drawer = auditDrawerRef.current;
    const focusFrame = window.requestAnimationFrame(() => drawer?.querySelector<HTMLElement>("button, a[href]")?.focus());

    function handleDrawerKeydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setSelected(null);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        drawer?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [],
      ).filter((element) => element.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", handleDrawerKeydown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener("keydown", handleDrawerKeydown);
      previous?.focus();
    };
  }, [selected?.id]);

  return (
    <PortalShell admin title="审计日志">
      <div className="audit-page-heading">
        <p className="page-intro">
          追踪谁在什么时间对哪个对象执行了什么操作。详情仅保留脱敏后的必要元数据；密码、完整 API Key、Authorization、Cookie 和上游 Token 不会展示。
        </p>
        <div className="audit-integrity-note"><ShieldCheck size={16} /> 审计记录只读，页面不提供修改或删除入口</div>
      </div>

      <div className="audit-overview" aria-label="审计查询摘要">
        <div><span>匹配记录</span><strong>{loading ? "-" : total}</strong></div>
        <div><span>当前范围</span><strong>第 {page} / {pageCount} 页</strong></div>
        <div><span>生效条件</span><strong>{activeFilterCount ? `${activeFilterCount} 项` : "全部记录"}</strong></div>
        <div><span>当前页操作人</span><strong>{new Set(items.map((item) => item.admin_username)).size || "-"}</strong></div>
      </div>

      <section className="panel audit-filter-panel">
        <form onSubmit={submit}>
          <div className="audit-filter-grid">
            <label className="form-field">
              动作分组
              <select
                value={filters.category}
                onChange={(event) => setFilters((current) => ({ ...current, category: event.target.value, action: "" }))}
              >
                <option value="">全部分组</option>
                {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="form-field">
              具体动作
              <select value={filters.action} onChange={(event) => setFilters((current) => ({ ...current, action: event.target.value }))}>
                <option value="">全部动作</option>
                {filteredActions.map((item) => <option key={item} value={item}>{actionLabels[item] || item}</option>)}
              </select>
            </label>
            <label className="form-field">
              操作人
              <input value={filters.adminUsername} onChange={(event) => setFilters((current) => ({ ...current, adminUsername: event.target.value }))} placeholder="按用户名模糊查询" />
            </label>
            <label className="form-field">
              对象类型
              <select value={filters.targetType} onChange={(event) => setFilters((current) => ({ ...current, targetType: event.target.value }))}>
                <option value="">全部对象</option>
                {availableTargetTypes.map((item) => <option key={item} value={item}>{targetLabels[item] || item}</option>)}
              </select>
            </label>
            <label className="form-field">
              对象 ID
              <input value={filters.targetId} onChange={(event) => setFilters((current) => ({ ...current, targetId: event.target.value }))} placeholder="精确匹配对象 ID" />
            </label>
            <label className="form-field">
              request_id
              <input value={filters.requestId} onChange={(event) => setFilters((current) => ({ ...current, requestId: event.target.value }))} placeholder="精确定位一次管理请求" />
            </label>
            <label className="form-field">
              开始时间
              <input type="datetime-local" value={filters.start} onChange={(event) => setFilters((current) => ({ ...current, start: event.target.value }))} />
            </label>
            <label className="form-field">
              结束时间
              <input type="datetime-local" value={filters.end} onChange={(event) => setFilters((current) => ({ ...current, end: event.target.value }))} />
            </label>
          </div>
          <div className="audit-filter-actions">
            <span className="muted">时间按当前浏览器时区输入，查询时会转换为标准时间。</span>
            <div className="toolbar-actions">
              <button className="button secondary compact" type="button" onClick={resetFilters} disabled={loading && !items.length}>
                <FilterX size={14} />清空
              </button>
              <button className="button compact" type="submit" disabled={loading}>
                <Search size={14} />{loading ? "查询中" : "查询"}
              </button>
            </div>
          </div>
        </form>
      </section>

      <section className="panel audit-list-panel">
        <div className="toolbar">
          <div>
            <h2>操作记录</h2>
            <p className="muted">按发生时间倒序排列；点击“详情”查看完整脱敏元数据。</p>
          </div>
          <button className="icon-button" type="button" title="刷新当前结果" aria-label="刷新当前结果" onClick={() => void load(page, appliedFilters, true)} disabled={loading}>
            <RefreshCw size={16} />
          </button>
        </div>
        {error && <div className="state-box error">{error}</div>}
        {loading && !items.length ? (
          <div className="state-box">正在加载审计记录...</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table audit-table">
              <thead>
                <tr>
                  <th>发生时间</th>
                  <th>动作</th>
                  <th>操作人</th>
                  <th>对象</th>
                  <th>请求追踪</th>
                  <th><span className="sr-only">操作</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="audit-time-cell">
                      <strong>{new Date(item.created_at).toLocaleDateString()}</strong>
                      <span>{new Date(item.created_at).toLocaleTimeString()}</span>
                    </td>
                    <td>
                      <span className={actionTone(item.action)}>{categoryLabels[actionCategory(item.action)] || "其他"}</span>
                      <strong className="audit-primary-text">{actionLabels[item.action] || item.action}</strong>
                      <span className="muted audit-action-code">{item.action}</span>
                    </td>
                    <td>
                      <span className="audit-actor"><UserRound size={15} />{item.admin_username}</span>
                      <span className="muted">{item.admin_user_id.slice(0, 8)}...</span>
                    </td>
                    <td>
                      <strong>{targetLabels[item.target_type] || item.target_type}</strong>
                      <span className="muted audit-target-id">{item.target_id || "无对象 ID"}</span>
                    </td>
                    <td>
                      <code className="audit-request-id">{item.request_id}</code>
                      <button className="text-icon-button" type="button" onClick={() => void copyToClipboard(item.request_id, "request_id 已复制")}>
                        <ClipboardCopy size={13} />复制
                      </button>
                    </td>
                    <td>
                      <button className="icon-button" type="button" title="查看审计详情" aria-label={`查看${actionLabels[item.action] || item.action}详情`} onClick={() => setSelected(item)}>
                        <Eye size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
                {!items.length && !loading && (
                  <tr><td colSpan={6}><div className="state-box">没有匹配记录。可清空筛选条件或调整时间范围后重试。</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        <div className="audit-pagination" aria-label="审计分页">
          <span className="muted">第 {page} 页，每页 {PAGE_SIZE} 条，共 {total} 条</span>
          <div className="toolbar-actions">
            <button className="button secondary compact" type="button" onClick={() => changePage(page - 1)} disabled={loading || page <= 1}>
              <ChevronLeft size={14} />上一页
            </button>
            <button className="button secondary compact" type="button" onClick={() => changePage(page + 1)} disabled={loading || !hasMore}>
              下一页<ChevronRight size={14} />
            </button>
          </div>
        </div>
      </section>

      {selected && (
        <div className="audit-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}>
          <aside ref={auditDrawerRef} className="audit-drawer" role="dialog" aria-modal="true" aria-labelledby="audit-detail-title">
            <header className="audit-drawer-header">
              <div>
                <span className={actionTone(selected.action)}>{categoryLabels[actionCategory(selected.action)] || "其他"}</span>
                <h2 id="audit-detail-title">{actionLabels[selected.action] || selected.action}</h2>
                <p className="muted">{new Date(selected.created_at).toLocaleString()} · {selected.admin_username}</p>
              </div>
              <button className="icon-button" type="button" aria-label="关闭审计详情" title="关闭" onClick={() => setSelected(null)}><X size={18} /></button>
            </header>

            <section className="audit-detail-section">
              <h3>追踪信息</h3>
              <dl className="audit-detail-list">
                <div><dt>动作代码</dt><dd><code>{selected.action}</code></dd></div>
                <div><dt>操作人</dt><dd>{selected.admin_username}<small>{selected.admin_user_id}</small></dd></div>
                <div><dt>对象</dt><dd>{targetLabels[selected.target_type] || selected.target_type}<small>{selected.target_id || "无对象 ID"}</small></dd></div>
                <div><dt>request_id</dt><dd><code>{selected.request_id}</code></dd></div>
              </dl>
              <div className="toolbar-actions">
                <button className="button secondary compact" type="button" onClick={() => void copyToClipboard(selected.request_id, "request_id 已复制")}><ClipboardCopy size={14} />复制 request_id</button>
                {targetLink(selected) && <Link className="button secondary compact" href={targetLink(selected) || "#"}><ExternalLink size={14} />查看关联对象</Link>}
              </div>
            </section>

            <section className="audit-detail-section">
              <h3>脱敏详情</h3>
              {Object.keys(selected.detail).length ? (
                <dl className="audit-metadata-list">
                  {Object.entries(selected.detail).map(([key, value]) => (
                    <div key={key}>
                      <dt>{detailLabel(key)}</dt>
                      <dd className={typeof value === "object" && value !== null ? "audit-complex-value" : ""}>{displayValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : <div className="state-box">该操作没有额外详情。</div>}
            </section>
          </aside>
        </div>
      )}
    </PortalShell>
  );
}
