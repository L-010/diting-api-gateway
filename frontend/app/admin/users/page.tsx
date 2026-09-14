"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useCallback, useDeferredValue, useEffect, useRef, useState } from "react";
import {
  Activity,
  Ban,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleUserRound,
  FileSpreadsheet,
  KeyRound,
  RefreshCw,
  Search,
  ShieldAlert,
  Upload,
  UserCheck,
  UserRoundX,
  X,
} from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

type UserState = "all" | "pending" | "active" | "disabled" | "rejected";

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
  api_key_count: number;
  active_api_key_count: number;
  last_api_activity_at: string | null;
  created_at: string;
  updated_at: string;
};

type AdminUserOverview = {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
  stats: { total: number; pending: number; active: number; disabled: number; rejected: number };
};

type ImportResult = {
  batch_id: string;
  total: number;
  accepted: number;
  rejected: number;
  errors: { line: number; message: string }[];
};

type AdminApiKey = {
  id: string;
  user_id: string;
  username: string;
  label: string;
  prefix: string;
  status: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  disabled_at: string | null;
  disable_reason: string;
  is_expired: boolean;
  can_enable: boolean;
  rotation_hint: string;
  recent_failure_count: number;
  recent_rate_limited_count: number;
  last_error_code: string | null;
  created_at: string;
};

type PendingAdminAction = {
  kind: "approve" | "reject" | "disable_user" | "enable_user" | "disable_key" | "enable_key";
  title: string;
  description: string;
  defaultReason: string;
  required: boolean;
  user?: AdminUser;
  key?: AdminApiKey;
};

const PAGE_SIZE = 20;

const stateOptions: { value: UserState; label: string; countKey: keyof AdminUserOverview["stats"] }[] = [
  { value: "all", label: "全部", countKey: "total" },
  { value: "pending", label: "待审批", countKey: "pending" },
  { value: "active", label: "已启用", countKey: "active" },
  { value: "disabled", label: "已停用", countKey: "disabled" },
  { value: "rejected", label: "已拒绝", countKey: "rejected" },
];

function formatDate(value: string | null | undefined, fallback = "暂无") {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? fallback : date.toLocaleString("zh-CN", { hour12: false });
}

function userState(user: AdminUser) {
  if (user.approval_status === "pending") return { label: "待审批", className: "status info" };
  if (user.approval_status === "rejected") return { label: "已拒绝", className: "status warn" };
  if (!user.is_active) return { label: "已停用", className: "status danger" };
  return { label: "已启用", className: "status good" };
}

function keyState(key: AdminApiKey) {
  if (key.is_expired) return { label: "已过期", className: "status warn" };
  if (key.status === "active") return { label: "可用", className: "status good" };
  if (key.status === "revoked") return { label: "已撤销", className: "status danger" };
  return { label: "已禁用", className: "status warn" };
}

function roleLabel(roles: string[]) {
  return roles.includes("admin") ? "管理员" : "普通用户";
}

export default function AdminUsersPage() {
  const [overview, setOverview] = useState<AdminUserOverview | null>(null);
  const [stateFilter, setStateFilter] = useState<UserState>("all");
  const [searchInput, setSearchInput] = useState("");
  const deferredSearch = useDeferredValue(searchInput.trim());
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null);
  const [userKeys, setUserKeys] = useState<AdminApiKey[]>([]);
  const [keyLoading, setKeyLoading] = useState(false);
  const [drawerError, setDrawerError] = useState("");
  const userDrawerRef = useRef<HTMLElement>(null);

  const [pendingAction, setPendingAction] = useState<PendingAdminAction | null>(null);
  const [actionReason, setActionReason] = useState("");
  const [busyAction, setBusyAction] = useState(false);

  const [keyPrefix, setKeyPrefix] = useState("");
  const [keySearchResults, setKeySearchResults] = useState<AdminApiKey[]>([]);
  const [keySearchDone, setKeySearchDone] = useState(false);
  const [keySearchLoading, setKeySearchLoading] = useState(false);

  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importing, setImporting] = useState(false);

  const loadOverview = useCallback(
    async (showToast = false, signal?: AbortSignal) => {
      if (showToast) setRefreshing(true);
      else setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({
          state: stateFilter,
          page: String(page),
          page_size: String(PAGE_SIZE),
        });
        if (deferredSearch) params.set("search", deferredSearch);
        const nextOverview = await api<AdminUserOverview>(`/api/admin/users/overview?${params}`, { signal });
        const nextTotalPages = Math.max(1, Math.ceil(nextOverview.total / PAGE_SIZE));
        if (page > nextTotalPages) {
          setPage(nextTotalPages);
          return;
        }
        setOverview(nextOverview);
        setSelectedUser((current) => {
          if (!current) return null;
          return nextOverview.items.find((item) => item.id === current.id) ?? current;
        });
        if (showToast) notifyToast({ type: "success", message: "用户数据已刷新" });
      } catch (cause) {
        if ((cause as { name?: string })?.name === "AbortError") return;
        const message = errorMessage(cause);
        setError(message);
        if (showToast) notifyToast({ type: "error", message });
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [deferredSearch, page, stateFilter],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadOverview(false, controller.signal);
    return () => controller.abort();
  }, [loadOverview]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const focusUserId = params.get("focus");
    const keyPrefixFromUrl = params.get("key_prefix")?.trim() || "";
    if (!focusUserId && keyPrefixFromUrl.length < 3) return;

    const userRequest = focusUserId
      ? api<AdminUser>(`/api/admin/users/${encodeURIComponent(focusUserId)}`)
      : api<AdminApiKey[]>(`/api/admin/api-keys?prefix=${encodeURIComponent(keyPrefixFromUrl)}`).then((keys) => {
          const key = keys.find((item) => item.prefix.startsWith(keyPrefixFromUrl));
          if (!key) throw new Error("未找到该 Key 对应的用户");
          setKeyPrefix(keyPrefixFromUrl);
          setKeySearchResults(keys);
          setKeySearchDone(true);
          return api<AdminUser>(`/api/admin/users/${encodeURIComponent(key.user_id)}`);
        });

    userRequest
      .then((user) => loadUserKeys(user))
      .catch((cause) => {
        const message = errorMessage(cause);
        setError(message);
        notifyToast({ type: "error", message });
      });
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || busyAction) return;
      if (pendingAction) setPendingAction(null);
      else if (selectedUser) setSelectedUser(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busyAction, pendingAction, selectedUser]);

  useEffect(() => {
    if (!selectedUser) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const drawer = userDrawerRef.current;
    const focusFrame = window.requestAnimationFrame(() => drawer?.querySelector<HTMLElement>("button, a[href]")?.focus());

    function trapFocus(event: KeyboardEvent) {
      if (event.key !== "Tab" || document.querySelector(".dialog-backdrop")) return;
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

    window.addEventListener("keydown", trapFocus);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener("keydown", trapFocus);
      previous?.focus();
    };
  }, [selectedUser?.id]);

  function chooseState(value: UserState) {
    setStateFilter(value);
    setPage(1);
  }

  function updateSearch(value: string) {
    setSearchInput(value);
    setPage(1);
  }

  function openAction(action: PendingAdminAction) {
    setPendingAction(action);
    setActionReason(action.defaultReason);
  }

  function approve(user: AdminUser) {
    openAction({
      kind: "approve",
      title: "通过用户申请",
      description: "通过后该用户可以立即登录、创建全局 API Key，并调用所有对普通用户开放的接口。",
      defaultReason: "资料完整，用途符合平台接入要求",
      required: false,
      user,
    });
  }

  function reject(user: AdminUser) {
    openAction({
      kind: "reject",
      title: "拒绝用户申请",
      description: "拒绝后该账号不能登录；原因会在用户使用正确密码登录时展示，请写清可补充或修正的内容。",
      defaultReason: "",
      required: true,
      user,
    });
  }

  function disable(user: AdminUser) {
    openAction({
      kind: "disable_user",
      title: "停用用户账号",
      description: "停用会立即使现有登录会话失效，并阻止该用户全部 API Key 的调用。Key 本身不会被删除。",
      defaultReason: "",
      required: true,
      user,
    });
  }

  function enable(user: AdminUser) {
    openAction({
      kind: "enable_user",
      title: "重新启用用户",
      description: "启用后用户可以重新登录；各 API Key 是否可用仍取决于 Key 自身状态和有效期。",
      defaultReason: "账号访问权限已恢复",
      required: false,
      user,
    });
  }

  function disableKey(key: AdminApiKey) {
    openAction({
      kind: "disable_key",
      title: `禁用 API Key ${key.prefix}...`,
      description: "禁用后使用该 Key 的程序会立即收到鉴权失败。管理员后续可以重新启用，但如密钥可能泄露，应让用户生成新 Key。",
      defaultReason: "",
      required: true,
      key,
    });
  }

  function enableKey(key: AdminApiKey) {
    openAction({
      kind: "enable_key",
      title: `重新启用 API Key ${key.prefix}...`,
      description: "重新启用后，原完整密钥会立即恢复可用。请确认禁用原因已消除；如密钥可能泄露，应让用户生成新 Key，不要恢复旧 Key。",
      defaultReason: "已确认该 API Key 可以恢复使用",
      required: false,
      key,
    });
  }

  async function loadUserKeys(user: AdminUser) {
    setSelectedUser(user);
    setUserKeys([]);
    setDrawerError("");
    setKeyLoading(true);
    try {
      setUserKeys(await api<AdminApiKey[]>(`/api/admin/users/${user.id}/api-keys`));
    } catch (cause) {
      setDrawerError(errorMessage(cause));
    } finally {
      setKeyLoading(false);
    }
  }

  async function submitPendingAction() {
    if (!pendingAction || busyAction) return;
    const reason = actionReason.trim() || pendingAction.defaultReason;
    if (pendingAction.required && reason.length < 2) {
      notifyToast({ type: "error", message: "请填写至少 2 个字符的原因" });
      return;
    }

    setBusyAction(true);
    setError("");
    try {
      if (pendingAction.kind === "disable_key" || pendingAction.kind === "enable_key") {
        if (!pendingAction.key) return;
        const enablingKey = pendingAction.kind === "enable_key";
        await api(`/api/admin/api-keys/${encodeURIComponent(pendingAction.key.id)}/${enablingKey ? "enable" : "disable"}`, {
          method: "PATCH",
          body: JSON.stringify({ reason }),
        });
        notifyToast({ type: "success", message: `Key ${pendingAction.key.prefix}... 已${enablingKey ? "重新启用" : "禁用"}` });
        if (selectedUser) await loadUserKeys(selectedUser);
        if (keySearchDone) await searchKeys(undefined, true);
      } else if (pendingAction.user) {
        const pathByKind = {
          approve: "approve",
          reject: "reject",
          disable_user: "disable",
          enable_user: "enable",
        } as const;
        const messageByKind = {
          approve: "用户申请已通过",
          reject: "用户申请已拒绝",
          disable_user: "用户账号已停用",
          enable_user: "用户账号已启用",
        } as const;
        const updated = await api<AdminUser>(
          `/api/admin/users/${pendingAction.user.id}/${pathByKind[pendingAction.kind]}`,
          { method: "POST", body: JSON.stringify({ reason }) },
        );
        setSelectedUser((current) => (current?.id === updated.id ? updated : current));
        notifyToast({ type: "success", message: `${updated.display_name || updated.username}：${messageByKind[pendingAction.kind]}` });
      }
      setPendingAction(null);
      await loadOverview();
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setBusyAction(false);
    }
  }

  async function searchKeys(event?: FormEvent<HTMLFormElement>, silent = false) {
    event?.preventDefault();
    const prefix = keyPrefix.trim();
    if (prefix.length < 3) {
      if (!silent) notifyToast({ type: "error", message: "请输入至少 3 个字符的 Key 前缀" });
      return;
    }
    setKeySearchLoading(true);
    setKeySearchDone(true);
    try {
      const rows = await api<AdminApiKey[]>(`/api/admin/api-keys?prefix=${encodeURIComponent(prefix)}`);
      setKeySearchResults(rows);
      if (!silent) notifyToast({ type: "success", message: `找到 ${rows.length} 条 Key 记录` });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      if (!silent) notifyToast({ type: "error", message });
    } finally {
      setKeySearchLoading(false);
    }
  }

  async function submitImport() {
    if (!file || importing) return;
    const data = new FormData();
    data.append("file", file);
    setImporting(true);
    setError("");
    try {
      const result = await api<ImportResult>("/api/admin/users/import", { method: "POST", body: data });
      setImportResult(result);
      setFile(null);
      setFileInputKey((current) => current + 1);
      await loadOverview();
      notifyToast({ type: "success", message: `CSV 导入完成：已创建 ${result.accepted}，失败 ${result.rejected}` });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setImporting(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil((overview?.total ?? 0) / PAGE_SIZE));

  return (
    <PortalShell admin title="用户管理">
      <div className="user-page-heading">
        <p className="page-intro">审批注册申请、控制账号访问并处置异常 API Key。所有状态变更均会写入管理员审计日志。</p>
        <button className="button secondary compact" type="button" onClick={() => void loadOverview(true)} disabled={refreshing}>
          <RefreshCw size={14} className={refreshing ? "spin" : undefined} />
          {refreshing ? "刷新中" : "刷新"}
        </button>
      </div>

      {error && <div className="state-box error" role="alert">{error}</div>}

      <section className="user-metrics" aria-label="用户状态概览">
        {stateOptions.map((option) => (
          <button
            type="button"
            key={option.value}
            className={stateFilter === option.value ? `user-metric selected ${option.value}` : `user-metric ${option.value}`}
            onClick={() => chooseState(option.value)}
            aria-pressed={stateFilter === option.value}
          >
            <span>{option.label}</span>
            <strong>{overview?.stats[option.countKey] ?? "-"}</strong>
            <small>{option.value === "pending" ? "需要处理" : option.value === "disabled" ? "当前不可登录" : "账号"}</small>
          </button>
        ))}
      </section>

      <section className="panel user-list-panel">
        <div className="user-list-toolbar">
          <div>
            <h2>{stateOptions.find((item) => item.value === stateFilter)?.label}用户</h2>
            <p className="muted">共 {overview?.total ?? 0} 条匹配结果{deferredSearch ? `，关键词“${deferredSearch}”` : ""}</p>
          </div>
          <label className="user-search">
            <Search size={16} />
            <input
              value={searchInput}
              onChange={(event) => updateSearch(event.target.value)}
              placeholder="搜索用户名、名称或邮箱"
              aria-label="搜索用户"
            />
            {searchInput && (
              <button type="button" className="search-clear" onClick={() => updateSearch("")} aria-label="清空搜索">
                <X size={14} />
              </button>
            )}
          </label>
        </div>

        <div className="user-state-tabs" role="tablist" aria-label="用户状态筛选">
          {stateOptions.map((option) => (
            <button
              type="button"
              role="tab"
              aria-selected={stateFilter === option.value}
              key={option.value}
              className={stateFilter === option.value ? "active" : undefined}
              onClick={() => chooseState(option.value)}
            >
              {option.label}<span>{overview?.stats[option.countKey] ?? 0}</span>
            </button>
          ))}
        </div>

        <div className="table-wrap user-table-wrap" aria-busy={loading}>
          <table className="data-table user-table">
            <thead>
              <tr>
                <th>用户</th>
                <th>账号状态</th>
                <th>API Key</th>
                <th>最近活动</th>
                <th><span className="sr-only">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {!loading && overview?.items.map((user) => {
                const state = userState(user);
                return (
                  <tr key={user.id}>
                    <td className="title-cell user-identity-cell">
                      <span className="user-avatar" aria-hidden="true">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</span>
                      <span>
                        <strong>{user.display_name || user.username}</strong>
                        <small>@{user.username}{user.email ? ` · ${user.email}` : ""}</small>
                      </span>
                    </td>
                    <td>
                      <span className={state.className}>{state.label}</span>
                      <small className="cell-note">{roleLabel(user.roles)}{user.must_change_password ? " · 待改密" : ""}</small>
                    </td>
                    <td>
                      <strong>{user.active_api_key_count} 可用</strong>
                      <small className="cell-note">共 {user.api_key_count} 个</small>
                    </td>
                    <td>
                      <span>{user.last_api_activity_at ? formatDate(user.last_api_activity_at) : "尚无 API 调用"}</span>
                      <small className="cell-note">创建于 {formatDate(user.created_at)}</small>
                    </td>
                    <td className="user-row-actions">
                      {user.approval_status === "pending" && (
                        <>
                          <button className="icon-button positive" type="button" onClick={() => approve(user)} aria-label={`通过 ${user.username} 的申请`} title="通过申请"><Check size={16} /></button>
                          <button className="icon-button" type="button" onClick={() => reject(user)} aria-label={`拒绝 ${user.username} 的申请`} title="拒绝申请"><X size={16} /></button>
                        </>
                      )}
                      <button className="button secondary compact" type="button" onClick={() => void loadUserKeys(user)}>查看</button>
                    </td>
                  </tr>
                );
              })}
              {loading && (
                <tr><td colSpan={5}><div className="state-box">正在加载用户数据...</div></td></tr>
              )}
              {!loading && !overview?.items.length && (
                <tr>
                  <td colSpan={5}>
                    <div className="user-empty-state">
                      <CircleUserRound size={32} />
                      <strong>{deferredSearch ? "没有匹配的用户" : "当前状态下没有用户"}</strong>
                      <span>{deferredSearch ? "请调整搜索关键词或切换状态。" : "新申请或状态变更后会显示在这里。"}</span>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="pagination-bar">
          <span>第 {Math.min(page, totalPages)} / {totalPages} 页</span>
          <div>
            <button className="icon-button" type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page <= 1 || loading} aria-label="上一页"><ChevronLeft size={16} /></button>
            <button className="icon-button" type="button" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page >= totalPages || loading} aria-label="下一页"><ChevronRight size={16} /></button>
          </div>
        </div>
      </section>

      <section className="admin-utilities">
        <details className="utility-panel">
          <summary><KeyRound size={18} /><span><strong>按 Key 前缀定位用户</strong><small>用于异常调用和泄露处置</small></span></summary>
          <div className="utility-content">
            <form className="key-search-form" onSubmit={(event) => void searchKeys(event)}>
              <div className="form-field">
                <label htmlFor="admin-key-prefix">API Key 前缀</label>
                <div className="input-action-row">
                  <input id="admin-key-prefix" value={keyPrefix} onChange={(event) => setKeyPrefix(event.target.value)} placeholder="至少输入 3 个字符" maxLength={24} />
                  <button className="button secondary" type="submit" disabled={keySearchLoading}>{keySearchLoading ? "检索中" : "检索"}</button>
                </div>
              </div>
            </form>
            {keySearchDone && !keySearchLoading && !keySearchResults.length && <div className="state-box">未找到匹配的 Key。</div>}
            {keySearchResults.map((key) => {
              const state = keyState(key);
              return (
                <div className="key-result-row" key={key.id}>
                  <span><strong>{key.username} · {key.label}</strong><small>{key.prefix}... · 近 24 小时失败 {key.recent_failure_count} 次，限流 {key.recent_rate_limited_count} 次</small></span>
                  <span className={state.className}>{state.label}</span>
                  {key.status === "active" && !key.is_expired && <button className="button secondary compact" type="button" onClick={() => disableKey(key)}>禁用</button>}
                  {key.status === "disabled" && !key.is_expired && key.can_enable && <button className="button compact" type="button" onClick={() => enableKey(key)}>重新启用</button>}
                </div>
              );
            })}
          </div>
        </details>

        <details className="utility-panel">
          <summary><FileSpreadsheet size={18} /><span><strong>CSV 批量开通</strong><small>适合已确认的内部用户清单</small></span></summary>
          <div className="utility-content">
            <div className="upload-zone compact-upload">
              <p>UTF-8 CSV，必填列 username、password；可选 display_name、email。最多 1 MiB，不能创建管理员。</p>
              <a href="/templates/user-import-template.csv" download="用户导入模板.csv" className="button secondary compact" style={{ marginBottom: 12 }}>
                <FileSpreadsheet size={16} />下载 CSV 模板
              </a>
              <label htmlFor="admin-user-csv-file">选择 CSV 文件</label>
              <input id="admin-user-csv-file" key={fileInputKey} type="file" accept=".csv,text/csv" onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)} />
              {file && <span className="selected-file">已选择：{file.name}</span>}
              <button className="button" type="button" disabled={!file || importing} onClick={() => void submitImport()}>
                <Upload size={16} />{importing ? "正在导入" : "导入并要求首登改密"}
              </button>
            </div>
            {importResult && (
              <div className="import-result" role="status">
                <span><strong>{importResult.total}</strong> 总行数</span>
                <span className="success"><strong>{importResult.accepted}</strong> 已创建</span>
                <span className="danger"><strong>{importResult.rejected}</strong> 失败</span>
                {importResult.errors.length > 0 && (
                  <div>{importResult.errors.slice(0, 10).map((item) => <p key={`${item.line}-${item.message}`}>第 {item.line} 行：{item.message}</p>)}</div>
                )}
              </div>
            )}
          </div>
        </details>
      </section>

      {selectedUser && (
        <div className="user-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busyAction) setSelectedUser(null); }}>
          <aside ref={userDrawerRef} className="user-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="user-detail-title">
            <header className="drawer-header">
              <div className="drawer-user-heading">
                <span className="user-avatar large" aria-hidden="true">{(selectedUser.display_name || selectedUser.username).slice(0, 1).toUpperCase()}</span>
                <div>
                  <span className={userState(selectedUser).className}>{userState(selectedUser).label}</span>
                  <h2 id="user-detail-title">{selectedUser.display_name || selectedUser.username}</h2>
                  <p>@{selectedUser.username} · {roleLabel(selectedUser.roles)}</p>
                </div>
              </div>
              <button className="icon-button" type="button" onClick={() => setSelectedUser(null)} aria-label="关闭用户详情"><X size={18} /></button>
            </header>

            <div className="drawer-body">
              {drawerError && <div className="state-box error" role="alert">{drawerError}</div>}
              <section className="drawer-section">
                <h3>账号信息</h3>
                <dl className="user-facts">
                  <div><dt>邮箱</dt><dd>{selectedUser.email || "未填写"}</dd></div>
                  <div><dt>首次登录改密</dt><dd>{selectedUser.must_change_password ? "需要" : "不需要"}</dd></div>
                  <div><dt>注册时间</dt><dd>{formatDate(selectedUser.registered_at || selectedUser.created_at)}</dd></div>
                  <div><dt>审批时间</dt><dd>{formatDate(selectedUser.approved_at)}</dd></div>
                </dl>
              </section>

              <section className="drawer-section">
                <h3>申请与状态说明</h3>
                <div className="user-note-block">
                  <span>用途说明</span>
                  <p>{selectedUser.registration_note || "未填写用途说明。"}</p>
                </div>
                {selectedUser.rejection_reason && <div className="user-note-block danger"><span>拒绝原因</span><p>{selectedUser.rejection_reason}</p></div>}
                {selectedUser.disable_reason && <div className="user-note-block danger"><span>停用原因</span><p>{selectedUser.disable_reason}</p><small>{formatDate(selectedUser.disabled_at)}</small></div>}
              </section>

              <section className="drawer-section">
                <div className="section-heading-row">
                  <div><h3>API Key</h3><p>{selectedUser.active_api_key_count} 个可用，共 {selectedUser.api_key_count} 个</p></div>
                  <Activity size={18} />
                </div>
                {keyLoading && <div className="state-box">正在加载 API Key...</div>}
                {!keyLoading && userKeys.map((key) => {
                  const state = keyState(key);
                  return (
                    <div className="drawer-key-row" key={key.id}>
                      <div><strong>{key.label}</strong><code>{key.prefix}...</code></div>
                      <span className={state.className}>{state.label}</span>
                      <dl>
                        <div><dt>最后使用</dt><dd>{formatDate(key.last_used_at, "尚未使用")}</dd></div>
                        <div><dt>近 24h</dt><dd>失败 {key.recent_failure_count} · 限流 {key.recent_rate_limited_count}</dd></div>
                        <div><dt>有效期</dt><dd>{formatDate(key.expires_at, "长期有效")}</dd></div>
                      </dl>
                      {key.disable_reason && <p className="key-disable-reason">禁用原因：{key.disable_reason}</p>}
                      {key.status === "active" && !key.is_expired && <button className="button secondary compact" type="button" onClick={() => disableKey(key)}>禁用此 Key</button>}
                      {key.status === "disabled" && !key.is_expired && key.can_enable && <button className="button compact" type="button" onClick={() => enableKey(key)}>重新启用此 Key</button>}
                    </div>
                  );
                })}
                {!keyLoading && !userKeys.length && <div className="state-box">该用户尚未创建 API Key。</div>}
              </section>
            </div>

            <footer className="drawer-actions">
              <Link className="button secondary" href={`/admin/users/${selectedUser.id}`}><Activity size={16} />完整档案</Link>
              {selectedUser.roles.includes("admin") ? (
                <p><ShieldAlert size={16} />管理员账号不能在此处变更审批和访问状态。</p>
              ) : selectedUser.approval_status === "pending" ? (
                <>
                  <button className="button secondary" type="button" onClick={() => reject(selectedUser)}><UserRoundX size={16} />拒绝</button>
                  <button className="button" type="button" onClick={() => approve(selectedUser)}><UserCheck size={16} />通过申请</button>
                </>
              ) : selectedUser.approval_status === "approved" && selectedUser.is_active ? (
                <button className="button danger-button" type="button" onClick={() => disable(selectedUser)}><Ban size={16} />停用账号</button>
              ) : selectedUser.approval_status === "approved" ? (
                <button className="button" type="button" onClick={() => enable(selectedUser)}><UserCheck size={16} />重新启用</button>
              ) : (
                <button className="button" type="button" onClick={() => approve(selectedUser)}><UserCheck size={16} />改为通过</button>
              )}
            </footer>
          </aside>
        </div>
      )}

      {pendingAction && (
        <div className="dialog-backdrop" role="presentation">
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="admin-action-title">
            <div className="toolbar">
              <div><span className="dialog-icon"><ShieldAlert size={18} /></span><h2 id="admin-action-title">{pendingAction.title}</h2></div>
              <button className="icon-button" type="button" aria-label="关闭" onClick={() => setPendingAction(null)} disabled={busyAction}><X size={18} /></button>
            </div>
            {(pendingAction.user || pendingAction.key) && (
              <div className="action-target">
                <span>操作对象</span>
                <strong>{pendingAction.user ? `${pendingAction.user.display_name || pendingAction.user.username}（@${pendingAction.user.username}）` : `${pendingAction.key?.username} · ${pendingAction.key?.prefix}...`}</strong>
              </div>
            )}
            <p className="muted dialog-description">{pendingAction.description}</p>
            <label className="form-field">
              原因{pendingAction.required ? "（必填）" : "（可修改）"}
              <textarea value={actionReason} onChange={(event) => setActionReason(event.target.value)} rows={4} maxLength={500} autoFocus />
              <small>{actionReason.trim().length}/500</small>
            </label>
            <div className="dialog-actions">
              <button className="button secondary" type="button" onClick={() => setPendingAction(null)} disabled={busyAction}>取消</button>
              <button className={pendingAction.kind === "reject" || pendingAction.kind === "disable_user" || pendingAction.kind === "disable_key" ? "button danger-button" : "button"} type="button" onClick={() => void submitPendingAction()} disabled={busyAction}>
                {busyAction ? "正在处理..." : "确认操作"}
              </button>
            </div>
          </section>
        </div>
      )}
    </PortalShell>
  );
}
