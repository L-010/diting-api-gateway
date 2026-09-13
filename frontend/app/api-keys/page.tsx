"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Copy, KeyRound, X } from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { copyToClipboard } from "@/lib/clipboard";
import { notifyToast } from "@/lib/toast";

type ApiKey = {
  id: string;
  label: string;
  prefix: string;
  status: string;
  scopes: string[];
  last_used_at: string | null;
  disabled_at: string | null;
  disabled_by_user_id: string | null;
  disable_reason: string;
  can_enable: boolean;
  is_expired: boolean;
  rotation_hint: string;
  created_at: string;
  expires_at: string | null;
  secret?: string;
};

function keyStatusLabel(key: ApiKey) {
  if (key.is_expired) return "已过期";
  if (key.status === "active") return "可用";
  if (key.status === "disabled") return "已禁用";
  return key.status;
}

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [disabling, setDisabling] = useState<Set<string>>(new Set());
  const [enabling, setEnabling] = useState<Set<string>>(new Set());
  const [duration, setDuration] = useState("90");
  const [permanentConfirmed, setPermanentConfirmed] = useState(false);
  const secretDialogRef = useRef<HTMLElement>(null);

  async function load() {
    try {
      setKeys(await api<ApiKey[]>("/api/me/api-keys"));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!secret) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const timer = window.setTimeout(() => setSecret(""), 60_000);
    window.requestAnimationFrame(() => secretDialogRef.current?.querySelector<HTMLElement>("button")?.focus());
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") setSecret("");
      if (event.key !== "Tab" || !secretDialogRef.current) return;
      const focusable = [...secretDialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), [tabindex]:not([tabindex="-1"])')];
      if (!focusable.length) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    window.addEventListener("keydown", handleKey);
    return () => { window.clearTimeout(timer); window.removeEventListener("keydown", handleKey); previous?.focus(); };
  }, [secret]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating) return;
    if (duration === "permanent" && !permanentConfirmed) { setError("永久 Key 必须确认风险后才能创建"); return; }
    setCreating(true);
    const form = event.currentTarget;
    setError("");
    try {
      const data = new FormData(form);
      const created = await api<ApiKey>("/api/me/api-keys", {
        method: "POST",
        body: JSON.stringify(duration === "permanent" ? { label: data.get("label"), permanent: true, expires_in_days: null } : { label: data.get("label"), expires_in_days: Number(duration) }),
      });
      setSecret(created.secret ?? "");
      await load();
      form.reset();
      setDuration("90");
      setPermanentConfirmed(false);
      notifyToast({ type: "success", message: "API Key 已生成，请立即复制保存" });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setCreating(false);
    }
  }

  async function disable(id: string) {
    if (disabling.has(id) || enabling.has(id)) return;
    if (!window.confirm("确认禁用该 API Key？禁用后使用它的程序会立即调用失败。")) return;
    setDisabling((current) => new Set(current).add(id));
    setError("");
    try {
      await api(`/api/me/api-keys/${id}/disable`, { method: "PATCH" });
      await load();
      notifyToast({ type: "success", message: "API Key 已禁用" });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setDisabling((current) => { const next = new Set(current); next.delete(id); return next; });
    }
  }

  async function enable(key: ApiKey) {
    if (enabling.has(key.id) || disabling.has(key.id)) return;
    if (!window.confirm(`确认重新启用 API Key“${key.label}”？\n\n重新启用后，原完整密钥会立即恢复可用。如密钥可能已经泄露，请生成新 Key，不要恢复旧 Key。`)) return;
    setEnabling((current) => new Set(current).add(key.id));
    setError("");
    try {
      await api(`/api/me/api-keys/${encodeURIComponent(key.id)}/enable`, { method: "PATCH" });
      await load();
      notifyToast({ type: "success", message: "API Key 已重新启用" });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    } finally {
      setEnabling((current) => { const next = new Set(current); next.delete(key.id); return next; });
    }
  }

  const activeCount = (keys ?? []).filter((key) => key.status === "active" && !key.is_expired).length;

  return (
    <PortalShell title="API 密钥">
      <p className="page-intro">
        新创建的 Key 默认作用域为 <code>*</code>，可调用全部已发布且工具启用的 Gateway 接口。数据库只保存哈希、前缀、状态和过期时间，完整密钥只展示一次。
      </p>
      <section className="panel" style={{ marginBottom: 22 }}>
        <div className="toolbar">
          <div>
            <h2>Key 使用边界</h2>
            <p className="muted">
              请把完整 Key 放在用户自己的后端服务、命令行环境变量或密钥管理系统中；不要写入浏览器前端源码、Git 仓库、URL query 或日志。
            </p>
          </div>
          <Link className="button secondary compact" href="/tools">
            选择工具与接口
          </Link>
        </div>
      </section>
      {secret && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSecret(""); }}><section ref={secretDialogRef} className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="new-key-title"><div className="toolbar"><h2 id="new-key-title">请立即安全保存新密钥</h2><button className="icon-button" type="button" aria-label="关闭并清除完整密钥" onClick={() => setSecret("")}><X size={17} /></button></div><p className="notice">完整密钥只展示一次，并将在 60 秒后自动从页面状态清除。关闭后无法找回。</p><div className="key-secret">{secret}</div><div className="dialog-actions" style={{ marginTop: 16 }}><button className="button secondary" type="button" onClick={() => void copyToClipboard(secret, "完整 API Key 已复制")}><Copy size={15} />复制密钥</button><button className="button" type="button" onClick={() => setSecret("")}>我已保存，关闭</button></div></section></div>}
      <div className="admin-layout">
        <section className="panel">
          <div className="toolbar">
            <h2>已创建的 Key</h2>
            <span className={activeCount > 1 ? "status warn" : "status good"}>{keys === null ? "加载中" : `${activeCount} 个 active`}</span>
          </div>
          <p className="muted">允许同时存在多个 Key，但建议生产集成只保留一个 active Key，轮换时先创建新 Key、切流后再禁用旧 Key。</p>
          {error && <p className="form-message">{error}</p>}
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>标签</th>
                  <th>前缀</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>最后使用</th>
                  <th>过期时间</th>
                  <th>轮换提示</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {(keys ?? []).map((key) => (
                  <tr key={key.id}>
                    <td className="title-cell">
                      <strong>{key.label}</strong>
                      <span className="muted">创建于 {new Date(key.created_at).toLocaleString()}</span>
                    </td>
                    <td>
                      <code>{key.prefix}...</code>
                      <br />
                      <button className="button secondary compact" style={{ marginTop: 8 }} onClick={() => void copyToClipboard(key.prefix, "Key 前缀已复制")}>
                        <Copy size={13} />复制前缀
                      </button>
                    </td>
                    <td>
                      <span className="status info">全局 Key</span>
                      <br />
                    </td>
                    <td>
                      <span className={key.status === "active" && !key.is_expired ? "status good" : "status warn"}>
                        {keyStatusLabel(key)}
                      </span>
                      {key.disabled_at && (
                        <>
                          <br />
                          <span className="muted">禁用：{new Date(key.disabled_at).toLocaleString()}</span>
                        </>
                      )}
                    </td>
                    <td>{key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "尚未使用"}</td>
                    <td>{key.expires_at ? new Date(key.expires_at).toLocaleString() : "长期有效"}</td>
                    <td>
                      <span className="muted">{key.rotation_hint || "如怀疑泄露，请生成新 Key 后禁用旧 Key。"}</span>
                      {key.disable_reason && (
                        <>
                          <br />
                          <span className="muted">原因：{key.disable_reason}</span>
                        </>
                      )}
                    </td>
                    <td>
                      {key.status === "active" && !key.is_expired && (
                        <button className="button secondary compact" type="button" disabled={disabling.has(key.id) || enabling.has(key.id)} onClick={() => void disable(key.id)}>
                          {disabling.has(key.id) ? "禁用中" : "禁用"}
                        </button>
                      )}
                      {key.status === "disabled" && !key.is_expired && key.can_enable && (
                        <button className="button compact" type="button" disabled={enabling.has(key.id) || disabling.has(key.id)} onClick={() => void enable(key)}>
                          {enabling.has(key.id) ? "启用中" : "重新启用"}
                        </button>
                      )}
                      {key.status === "disabled" && !key.is_expired && !key.can_enable && <span className="muted">请联系管理员</span>}
                    </td>
                  </tr>
                ))}
                {keys === null && !error && (
                  <tr>
                    <td colSpan={8}>
                      <div className="table-loading-state" role="status">正在加载 API Key...</div>
                    </td>
                  </tr>
                )}
                {keys !== null && !keys.length && (
                  <tr>
                    <td colSpan={8}>
                      <div className="state-box">尚未创建 API Key。审批通过并登录后，可在右侧生成第一个全局 Key。</div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
        <form className="panel" onSubmit={create}>
          <KeyRound size={24} color="#1a5ec0" />
          <h2 style={{ marginTop: 12 }}>生成全局 Key</h2>
          <p className="muted">Key 只用于平台 Gateway 鉴权，不会等同或透传给实际调用工具。</p>
          <label className="form-field">
            Key 标签
            <input name="label" placeholder="例如：本地 Python 集成" required maxLength={80} />
          </label>
          <label className="form-field">有效期<select value={duration} onChange={(event) => { setDuration(event.target.value); if (event.target.value !== "permanent") setPermanentConfirmed(false); }}><option value="30">30 天</option><option value="90">90 天（默认）</option><option value="180">180 天</option><option value="365">365 天</option><option value="permanent">永久有效</option></select></label>
          {duration === "permanent" && <label className="checkbox-field"><input type="checkbox" checked={permanentConfirmed} onChange={(event) => setPermanentConfirmed(event.target.checked)} /><span>我确认永久 Key 不会自动失效，并会自行执行定期轮换。</span></label>}
          <button className="button" type="submit" disabled={creating || (duration === "permanent" && !permanentConfirmed)} style={{ width: "100%" }}>
            {creating ? "生成中..." : "生成并只显示一次"}
          </button>
        </form>
      </div>
    </PortalShell>
  );
}
