"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useState } from "react";
import { Globe2, ImagePlus, Mail, PlugZap, Save, Send, ShieldCheck, UsersRound, Trash2 } from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { notifyToast } from "@/lib/toast";

type SettingsTab = "gateway" | "email" | "users";
type PlatformSettings = {
  smtp_enabled: boolean;
  smtp_configured: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password_configured: boolean;
  smtp_from_email: string;
  smtp_from_name: string;
  smtp_use_tls: boolean;
  default_user_rate_limit_per_minute: number;
  default_user_storage_quota_bytes: number;
  default_file_retention_days: number;
  public_gateway_base_url: string;
  gateway_source: "browser" | "environment" | "database";
  source: "environment" | "database";
  updated_at: string | null;
  site_name: string;
  site_subtitle: string;
  registration_enabled: boolean;
  brand_image_url: string;
};

type EmailForm = {
  smtp_enabled: boolean;
  smtp_host: string;
  smtp_port: string;
  smtp_username: string;
  smtp_password: string;
  smtp_from_email: string;
  smtp_from_name: string;
  smtp_use_tls: boolean;
  clear_password: boolean;
};

function emailFormFrom(settings: PlatformSettings): EmailForm {
  return {
    smtp_enabled: settings.smtp_enabled,
    smtp_host: settings.smtp_host,
    smtp_port: String(settings.smtp_port),
    smtp_username: settings.smtp_username,
    smtp_password: "",
    smtp_from_email: settings.smtp_from_email,
    smtp_from_name: settings.smtp_from_name,
    smtp_use_tls: settings.smtp_use_tls,
    clear_password: false,
  };
}

export default function AdminSettingsPage() {
  const [tab, setTab] = useState<SettingsTab>("gateway");
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [emailForm, setEmailForm] = useState<EmailForm | null>(null);
  const [defaultRateLimit, setDefaultRateLimit] = useState("0");
  const [defaultStorageQuotaGiB, setDefaultStorageQuotaGiB] = useState("100");
  const [defaultRetentionDays, setDefaultRetentionDays] = useState("90");
  const [gatewayBaseUrl, setGatewayBaseUrl] = useState("");
  const [siteName, setSiteName] = useState("");
  const [siteSubtitle, setSiteSubtitle] = useState("");
  const [registrationEnabled, setRegistrationEnabled] = useState(true);
  const [testRecipient, setTestRecipient] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const next = await api<PlatformSettings>("/api/admin/settings");
      setSettings(next);
      setEmailForm(emailFormFrom(next));
      setDefaultRateLimit(String(next.default_user_rate_limit_per_minute));
      setDefaultStorageQuotaGiB(String(next.default_user_storage_quota_bytes / 1_073_741_824));
      setDefaultRetentionDays(String(next.default_file_retention_days));
      setGatewayBaseUrl(next.public_gateway_base_url);
      setSiteName(next.site_name);
      setSiteSubtitle(next.site_subtitle);
      setRegistrationEnabled(next.registration_enabled);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const queryTab = new URLSearchParams(window.location.search).get("tab");
    if (queryTab === "email" || queryTab === "users" || queryTab === "gateway") setTab(queryTab);
    void load();
  }, [load]);

  function chooseTab(next: SettingsTab) {
    setTab(next);
    window.history.replaceState(null, "", `/admin/settings?tab=${next}`);
  }

  function updateEmail<K extends keyof EmailForm>(key: K, value: EmailForm[K]) {
    setEmailForm((current) => current ? { ...current, [key]: value } : current);
  }

  async function saveEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!emailForm || busy) return;
    setBusy("email");
    try {
      const next = await api<PlatformSettings>("/api/admin/settings/email", {
        method: "PUT",
        body: JSON.stringify({
          ...emailForm,
          smtp_port: Number(emailForm.smtp_port),
          smtp_password: emailForm.smtp_password || null,
        }),
      });
      setSettings(next);
      setEmailForm(emailFormFrom(next));
      notifyToast({ type: "success", message: "邮箱配置已保存并即时生效" });
    } catch (cause) {
      notifyToast({ type: "error", message: "邮箱配置保存失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function saveUserDefaults(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const value = Number(defaultRateLimit);
    const quotaGiB = Number(defaultStorageQuotaGiB);
    const retentionDays = Number(defaultRetentionDays);
    if (!Number.isInteger(value) || value < 0 || value > 10_000) {
      notifyToast({ type: "error", message: "每分钟请求上限必须是 0 到 10000 的整数" });
      return;
    }
    if (!Number.isInteger(quotaGiB) || quotaGiB < 1 || quotaGiB > 102_400 || !Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 3_650) {
      notifyToast({ type: "error", message: "默认容量必须为 1 到 102400 GiB，保留期必须为 1 到 3650 天" });
      return;
    }
    setBusy("users");
    try {
      const next = await api<PlatformSettings>("/api/admin/settings/user-defaults", {
        method: "PUT",
        body: JSON.stringify({ rate_limit_per_minute: value, storage_quota_bytes: quotaGiB * 1_073_741_824, file_retention_days: retentionDays }),
      });
      setSettings(next);
      setDefaultRateLimit(String(next.default_user_rate_limit_per_minute));
      setDefaultStorageQuotaGiB(String(next.default_user_storage_quota_bytes / 1_073_741_824));
      setDefaultRetentionDays(String(next.default_file_retention_days));
      notifyToast({ type: "success", message: "新用户默认限流已更新" });
    } catch (cause) {
      notifyToast({ type: "error", message: "默认设置保存失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function saveGateway(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const value = gatewayBaseUrl.trim().replace(/\/$/, "");
    if (value && !/^https?:\/\/[^/?#]+$/i.test(value)) {
      notifyToast({ type: "error", message: "统一网关地址必须是 http/https 根地址，不能包含路径、查询参数或凭据" });
      return;
    }
    setBusy("gateway");
    try {
      const next = await api<PlatformSettings>("/api/admin/settings/gateway", {
        method: "PUT",
        body: JSON.stringify({ public_gateway_base_url: value }),
      });
      setSettings(next);
      setGatewayBaseUrl(next.public_gateway_base_url);
      notifyToast({ type: "success", message: "用户侧统一网关地址已更新" });
    } catch (cause) {
      notifyToast({ type: "error", message: "统一网关地址保存失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function saveSite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !siteName.trim()) return;
    setBusy("site");
    try {
      const next = await api<PlatformSettings>("/api/admin/settings/site", { method: "PUT", body: JSON.stringify({ site_name: siteName.trim(), site_subtitle: siteSubtitle.trim(), registration_enabled: registrationEnabled }) });
      setSettings(next);
      setSiteName(next.site_name); setSiteSubtitle(next.site_subtitle); setRegistrationEnabled(next.registration_enabled);
      notifyToast({ type: "success", message: "站点品牌与注册设置已保存" });
    } catch (cause) { notifyToast({ type: "error", message: "站点设置保存失败", details: errorMessage(cause) }); }
    finally { setBusy(""); }
  }

  async function uploadBrandImage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || busy) return;
    setBusy("brand");
    try {
      const body = new FormData(); body.set("file", file);
      const next = await api<PlatformSettings>("/api/admin/settings/brand-image", { method: "POST", body });
      setSettings(next); notifyToast({ type: "success", message: "品牌图片已更新" });
    } catch (cause) { notifyToast({ type: "error", message: "品牌图片上传失败", details: errorMessage(cause) }); }
    finally { setBusy(""); }
  }

  async function removeBrandImage() {
    if (busy || !settings?.brand_image_url || !window.confirm("确认移除当前品牌图片？")) return;
    setBusy("brand");
    try { const next = await api<PlatformSettings>("/api/admin/settings/brand-image", { method: "DELETE" }); setSettings(next); notifyToast({ type: "success", message: "品牌图片已移除" }); }
    catch (cause) { notifyToast({ type: "error", message: "品牌图片移除失败", details: errorMessage(cause) }); }
    finally { setBusy(""); }
  }

  async function testConnection() {
    if (busy) return;
    setBusy("connection");
    try {
      const result = await api<{ message: string }>("/api/admin/settings/email/test-connection", { method: "POST" });
      notifyToast({ type: "success", message: result.message });
    } catch (cause) {
      notifyToast({ type: "error", message: "SMTP 连接测试失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function sendTestEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !testRecipient.trim()) return;
    setBusy("test-email");
    try {
      const result = await api<{ message: string }>("/api/admin/settings/email/test-message", {
        method: "POST",
        body: JSON.stringify({ recipient: testRecipient.trim() }),
      });
      notifyToast({ type: "success", message: result.message, details: "请保持邮件发送进程运行，并可在监控告警中查看投递状态。" });
      setTestRecipient("");
    } catch (cause) {
      notifyToast({ type: "error", message: "测试邮件入队失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  return (
    <PortalShell admin title="平台设置">
      <p className="page-intro">集中管理用户可见的网关入口、邮件能力和新用户默认策略。所有修改都会写入审计日志。</p>
      <div className="settings-tabs" role="tablist" aria-label="平台设置分类">
        <button type="button" role="tab" aria-selected={tab === "gateway"} className={tab === "gateway" ? "active" : ""} onClick={() => chooseTab("gateway")}><Globe2 size={17} />网关与域名</button>
        <button type="button" role="tab" aria-selected={tab === "email"} className={tab === "email" ? "active" : ""} onClick={() => chooseTab("email")}><Mail size={17} />邮箱配置</button>
        <button type="button" role="tab" aria-selected={tab === "users"} className={tab === "users" ? "active" : ""} onClick={() => chooseTab("users")}><UsersRound size={17} />用户默认设置</button>
      </div>

      {loading && <div className="state-box">正在加载平台设置...</div>}
      {error && <div className="state-box error" role="alert"><p>{error}</p><button className="button secondary compact" type="button" onClick={() => void load()}>重试</button></div>}

      {!loading && settings && tab === "gateway" && (
        <div className="settings-stack">
        <form className="panel settings-section settings-default-section" onSubmit={saveSite}>
          <div className="settings-section-heading"><div><h2><ImagePlus size={19} />站点品牌与注册</h2><p>统一控制公开页面、登录注册页、邮件模板和用户/管理员工作台显示内容。</p></div></div>
          <div className="settings-form-grid">
            <label className="form-field">站点名称<input value={siteName} onChange={(event) => setSiteName(event.target.value)} maxLength={128} required /><small>显示在页面标题、邮件和品牌区域。</small></label>
            <label className="form-field">站点副标题<input value={siteSubtitle} onChange={(event) => setSiteSubtitle(event.target.value)} maxLength={255} /><small>显示在登录页和工作台品牌区域。</small></label>
          </div>
          <label className="settings-toggle-row"><span><strong>开放注册</strong><small>关闭后新用户不能提交注册申请；邮件服务未配置时注册仍会自动关闭。</small></span><input type="checkbox" checked={registrationEnabled} onChange={(event) => setRegistrationEnabled(event.target.checked)} /></label>
          <div className="settings-actions"><span>保存后立即生效</span><button className="button" type="submit" disabled={Boolean(busy)}><Save size={15} />{busy === "site" ? "保存中" : "保存站点设置"}</button></div>
        </form>
        <form className="panel settings-section settings-default-section" onSubmit={saveGateway}>
          <div className="settings-section-heading"><div><h2><Globe2 size={19} />用户侧统一网关</h2><p>用于工具文档、复制地址和 curl、Python、JavaScript 示例。所有已发布工具共用这一入口。</p></div><span className={settings.public_gateway_base_url ? "status good" : "status warn"}>{settings.public_gateway_base_url ? "已配置" : "使用回退"}</span></div>
          <div className="settings-policy-row">
            <ShieldCheck size={22} />
            <label className="form-field">公开网关根地址<input value={gatewayBaseUrl} onChange={(event) => setGatewayBaseUrl(event.target.value)} type="url" inputMode="url" maxLength={512} placeholder="https://api.example.com" /><small>只填写协议、域名和可选端口，不要填写 /gateway。生产环境必须使用 HTTPS；留空会回退到环境变量或用户当前访问域名。</small></label>
          </div>
          <div className="settings-boundary-note"><strong>部署边界</strong><p>保存后只改变平台生成的公开调用地址，不会自动修改 DNS、TLS 证书、Caddy/Nginx 或云负载均衡。切换前应先确认新域名已正确转发到本平台。</p></div>
          <div className="settings-actions"><span>当前来源：{settings.gateway_source === "database" ? "数据库平台设置" : settings.gateway_source === "environment" ? "PUBLIC_GATEWAY_BASE_URL 环境变量" : "用户当前访问域名"}</span><button className="button" type="submit" disabled={Boolean(busy)}><Save size={15} />{busy === "gateway" ? "保存中" : "保存网关地址"}</button></div>
        </form>
        <section className="panel settings-section settings-default-section">
          <div className="settings-section-heading"><div><h2><ImagePlus size={19} />品牌图片</h2><p>左上角品牌标识使用的图片。支持 PNG、JPEG、WebP，最大 2MB。</p></div></div>
          <div className="brand-image-settings">
            {settings.brand_image_url ? <img src={settings.brand_image_url} alt="当前品牌图片" className="brand-image-preview" /> : <div className="brand-image-empty">尚未上传品牌图片</div>}
            <div className="toolbar-actions"><label className="button secondary compact"><ImagePlus size={15} />{busy === "brand" ? "处理中" : "上传图片"}<input type="file" accept="image/png,image/jpeg,image/webp" onChange={uploadBrandImage} disabled={Boolean(busy)} hidden /></label>{settings.brand_image_url && <button className="button secondary compact" type="button" onClick={() => void removeBrandImage()} disabled={Boolean(busy)}><Trash2 size={15} />移除图片</button>}</div>
          </div>
        </section>
        </div>
      )}

      {!loading && settings && emailForm && tab === "email" && (
        <div className="settings-stack">
          <form className="panel settings-section" onSubmit={saveEmail}>
            <div className="settings-section-heading">
              <div><h2><Mail size={19} />SMTP 设置</h2><p>用于邮箱验证、密码找回、审批结果和安全通知。邮件正文使用平台内置模板。</p></div>
              <div className="toolbar-actions">
                <span className={settings.smtp_configured ? "status good" : "status warn"}>{settings.smtp_configured ? "服务可用" : "未启用"}</span>
                <button className="button secondary compact" type="button" disabled={Boolean(busy) || !settings.smtp_configured} onClick={() => void testConnection()}><PlugZap size={15} />{busy === "connection" ? "测试中" : "测试已保存配置"}</button>
              </div>
            </div>
            <label className="settings-toggle-row">
              <span><strong>启用邮件服务</strong><small>关闭后公开注册、找回密码和邮箱变更会同步关闭，已保存参数不会被删除。</small></span>
              <input type="checkbox" checked={emailForm.smtp_enabled} onChange={(event) => updateEmail("smtp_enabled", event.target.checked)} />
            </label>
            <div className="settings-form-grid">
              <label className="form-field">SMTP 主机<input value={emailForm.smtp_host} onChange={(event) => updateEmail("smtp_host", event.target.value)} placeholder="smtp.example.com" maxLength={255} required={emailForm.smtp_enabled} /></label>
              <label className="form-field">SMTP 端口<input value={emailForm.smtp_port} onChange={(event) => updateEmail("smtp_port", event.target.value)} type="number" min={1} max={65535} inputMode="numeric" required /></label>
              <label className="form-field">SMTP 用户名<input value={emailForm.smtp_username} onChange={(event) => updateEmail("smtp_username", event.target.value)} autoComplete="username" maxLength={255} /></label>
              <label className="form-field">SMTP 密码<input value={emailForm.smtp_password} onChange={(event) => updateEmail("smtp_password", event.target.value)} type="password" autoComplete="new-password" placeholder={settings.smtp_password_configured ? "留空保留现有密码" : "输入 SMTP 密码"} maxLength={1024} disabled={emailForm.clear_password} /><small>{settings.smtp_password_configured ? "密码已加密保存，接口不会返回原文。" : "尚未配置密码。"}</small></label>
              <label className="form-field">发件人邮箱<input value={emailForm.smtp_from_email} onChange={(event) => updateEmail("smtp_from_email", event.target.value)} type="email" maxLength={255} required={emailForm.smtp_enabled} /></label>
              <label className="form-field">发件人名称<input value={emailForm.smtp_from_name} onChange={(event) => updateEmail("smtp_from_name", event.target.value)} maxLength={128} placeholder="API Gateway" /></label>
            </div>
            <div className="settings-options-row">
              <label><input type="checkbox" checked={emailForm.smtp_use_tls} onChange={(event) => updateEmail("smtp_use_tls", event.target.checked)} /><span><strong>使用 STARTTLS</strong><small>连接后升级为 TLS 加密，常用于 587 端口。</small></span></label>
              {settings.smtp_password_configured && <label><input type="checkbox" checked={emailForm.clear_password} onChange={(event) => updateEmail("clear_password", event.target.checked)} /><span><strong>清除已保存密码</strong><small>仅在确认服务不再需要认证时使用。</small></span></label>}
            </div>
            <div className="settings-actions"><span>当前来源：{settings.source === "database" ? "数据库平台设置" : "环境变量兼容配置"}{settings.updated_at ? ` · 更新于 ${new Date(settings.updated_at).toLocaleString()}` : ""}</span><button className="button" type="submit" disabled={Boolean(busy)}><Save size={15} />{busy === "email" ? "保存中" : "保存邮箱配置"}</button></div>
          </form>

          <form className="panel settings-section" onSubmit={sendTestEmail}>
            <div className="settings-section-heading"><div><h2><Send size={19} />发送测试邮件</h2><p>使用内置测试模板加入邮件 Outbox，验证完整投递链路。</p></div></div>
            <div className="settings-test-row"><label className="form-field">收件人邮箱<input value={testRecipient} onChange={(event) => setTestRecipient(event.target.value)} type="email" placeholder="test@example.com" required /></label><button className="button secondary" type="submit" disabled={Boolean(busy) || !settings.smtp_configured}><Send size={15} />{busy === "test-email" ? "入队中" : "发送测试邮件"}</button></div>
          </form>
        </div>
      )}

      {!loading && settings && tab === "users" && (
        <form className="panel settings-section settings-default-section" onSubmit={saveUserDefaults}>
          <div className="settings-section-heading"><div><h2><UsersRound size={19} />新用户默认设置</h2><p>只影响保存后新注册或新导入的普通用户，不会批量修改已有用户。</p></div></div>
          <div className="settings-policy-row">
            <ShieldCheck size={22} />
            <div className="settings-form-grid"><label className="form-field">每分钟请求上限<input value={defaultRateLimit} onChange={(event) => setDefaultRateLimit(event.target.value)} type="number" min={0} max={10000} step={1} inputMode="numeric" required /><small>填写 0 表示不增加用户级限制。工具自身的保护仍然生效。</small></label><label className="form-field">默认文件配额（GiB）<input value={defaultStorageQuotaGiB} onChange={(event) => setDefaultStorageQuotaGiB(event.target.value)} type="number" min={1} max={102400} step={1} inputMode="numeric" required/><small>按未删除文件的确认大小统计，不自动删除用户数据。</small></label><label className="form-field">默认文件保留期（天）<input value={defaultRetentionDays} onChange={(event) => setDefaultRetentionDays(event.target.value)} type="number" min={1} max={3650} step={1} inputMode="numeric" required/><small>工具声明更短期限时取较早值。</small></label></div>
          </div>
          <div className="settings-actions"><span>默认值只用于新用户；已有用户的独立覆盖不受影响。</span><button className="button" type="submit" disabled={Boolean(busy)}><Save size={15} />{busy === "users" ? "保存中" : "保存默认设置"}</button></div>
        </form>
      )}
    </PortalShell>
  );
}
