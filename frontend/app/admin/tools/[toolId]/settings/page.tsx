"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { ArrowLeft, Gauge, HardDrive, KeyRound, Network, PlugZap, RefreshCw, RotateCcw, Save, Server, ShieldAlert } from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { ToolConfiguration, ToolConfigurationRevision, statusLabel } from "@/lib/admin-tools";
import { notifyToast } from "@/lib/toast";

type ConfigurationForm = Omit<ToolConfiguration, "tool_id" | "slug" | "is_enabled" | "token_configured" | "config_revision" | "updated_at" | "file_quarantined"> & {
  upstream_token: string;
  clear_upstream_token: boolean;
};

type TestResult = {
  health: { status?: string; reachable?: boolean; status_code?: number | null; path?: string };
  changed_fields: string[];
  requires_health_check: boolean;
  candidate_base_url_masked: string;
};

type Onboarding = { allowed_upstream_hosts: string[] };
type StorageEndpoint = { id:string; revision:number; base_url_masked:string; auth_type:string; token_configured:boolean; verify_tls:boolean; is_active:boolean; file_count:number; task_count:number; pending_delete_count:number; created_at:string; retired_at:string|null };

function formFrom(configuration: ToolConfiguration): ConfigurationForm {
  return {
    name: configuration.name,
    description: configuration.description,
    status: configuration.status,
    default_gateway_prefix: configuration.default_gateway_prefix,
    base_url: configuration.base_url,
    health_path: configuration.health_path,
    auth_type: configuration.auth_type,
    token_label: configuration.token_label,
    rate_limit_per_minute: configuration.rate_limit_per_minute,
    network_zone: configuration.network_zone,
    verify_tls: configuration.verify_tls,
    retry_count: configuration.retry_count,
    connect_timeout_seconds: configuration.connect_timeout_seconds,
    request_timeout_seconds: configuration.request_timeout_seconds,
    network_isolation_mode: configuration.network_isolation_mode,
    network_isolation_note: configuration.network_isolation_note,
    network_isolation_confirmed: configuration.network_isolation_confirmed,
    file_retention_days: configuration.file_retention_days,
    storage_quota_bytes: configuration.storage_quota_bytes,
    max_file_bytes: configuration.max_file_bytes,
    file_access_enabled: configuration.file_access_enabled,
    storage_risk_acknowledged: configuration.storage_risk_acknowledged,
    upstream_token: "",
    clear_upstream_token: false,
  };
}

const fieldLabels: Record<string, string> = {
  name: "工具名称",
  description: "工具说明",
  status: "运行状态",
  default_gateway_prefix: "默认网关前缀",
  base_url: "上游根地址",
  health_path: "健康检查路径",
  auth_type: "认证方式",
  token_label: "认证请求头",
  upstream_token: "上游密钥",
  rate_limit_per_minute: "工具级限流",
  network_zone: "网络区域",
  verify_tls: "TLS 校验",
  retry_count: "重试次数",
  connect_timeout_seconds: "连接超时",
  request_timeout_seconds: "请求超时",
  network_isolation_mode: "网络隔离方式",
  network_isolation_note: "网络隔离说明",
  network_isolation_confirmed: "网络隔离确认",
  file_retention_days: "文件保留期",
  storage_quota_bytes: "工具容量限制",
  max_file_bytes: "单文件上限",
  file_access_enabled: "历史文件访问",
  storage_risk_acknowledged: "无水位能力风险确认",
};

export default function ToolSettingsPage() {
  const params = useParams<{ toolId: string }>();
  const toolId = params.toolId;
  const [configuration, setConfiguration] = useState<ToolConfiguration | null>(null);
  const [form, setForm] = useState<ConfigurationForm | null>(null);
  const [revisions, setRevisions] = useState<ToolConfigurationRevision[]>([]);
  const [allowedHosts, setAllowedHosts] = useState<string[]>([]);
  const [storageEndpoints, setStorageEndpoints] = useState<StorageEndpoint[]>([]);
  const [reason, setReason] = useState("");
  const [confirmSave, setConfirmSave] = useState(false);
  const [rollbackReason, setRollbackReason] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [next, nextRevisions, onboarding, endpoints] = await Promise.all([
        api<ToolConfiguration>(`/api/admin/tools/${toolId}/configuration`),
        api<ToolConfigurationRevision[]>(`/api/admin/tools/${toolId}/configuration/revisions`),
        api<Onboarding>("/api/admin/platform/onboarding"),
        api<StorageEndpoint[]>(`/api/admin/tools/${toolId}/storage-endpoints`),
      ]);
      setConfiguration(next);
      setForm(formFrom(next));
      setRevisions(nextRevisions);
      setAllowedHosts(onboarding.allowed_upstream_hosts || []);
      setStorageEndpoints(endpoints);
      setReason("");
      setConfirmSave(false);
      setTestResult(null);
      setDirty(false);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, [toolId]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  function update<K extends keyof ConfigurationForm>(key: K, value: ConfigurationForm[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current);
    setDirty(true);
    setTestResult(null);
    setConfirmSave(false);
  }

  function validateForm(requireReason = false) {
    if (!form) return "配置尚未加载";
    if (!/^https?:\/\/[^/?#]+$/i.test(form.base_url.trim())) return "上游根地址必须是 http/https 根地址，且不能包含路径、查询参数或凭据";
    if (form.auth_type === "header_api_key" && !form.token_label.trim()) return "Header API Key 模式必须填写请求头名称";
    if (form.auth_type !== "none" && !configuration?.token_configured && !form.upstream_token.trim()) return "当前认证方式必须配置上游密钥";
    if (requireReason && reason.trim().length < 4) return "变更原因至少填写 4 个字符";
    return "";
  }

  async function testCandidate() {
    if (!form || busy) return;
    const validationError = validateForm();
    if (validationError) { notifyToast({ type: "error", message: validationError }); return; }
    setBusy("test");
    try {
      const result = await api<TestResult>(`/api/admin/tools/${toolId}/configuration/test`, {
        method: "POST",
        body: JSON.stringify(form),
      });
      setTestResult(result);
      notifyToast({
        type: result.health.reachable ? "success" : "error",
        message: result.health.reachable ? "候选上游连接正常" : "候选上游当前不可达",
        details: `状态码：${result.health.status_code ?? "无响应"}；变化字段：${result.changed_fields.length}`,
      });
    } catch (cause) {
      notifyToast({ type: "error", message: "候选配置测试失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form || !configuration || busy) return;
    const validationError = validateForm(true);
    if (validationError) { notifyToast({ type: "error", message: validationError }); return; }
    if (!confirmSave) { notifyToast({ type: "error", message: "请先确认配置影响范围" }); return; }
    setBusy("save");
    try {
      const next = await api<ToolConfiguration>(`/api/admin/tools/${toolId}/configuration`, {
        method: "PATCH",
        body: JSON.stringify({ ...form, expected_revision: configuration.config_revision, reason: reason.trim(), confirm: true }),
      });
      setConfiguration(next);
      setForm(formFrom(next));
      setReason("");
      setConfirmSave(false);
      setTestResult(null);
      setDirty(false);
      setRevisions(await api<ToolConfigurationRevision[]>(`/api/admin/tools/${toolId}/configuration/revisions`));
      notifyToast({ type: "success", message: "工具运行配置已保存", details: `当前配置版本：${next.config_revision}` });
    } catch (cause) {
      notifyToast({ type: "error", message: "工具配置保存失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function rollback(revision: ToolConfigurationRevision) {
    if (!configuration || busy) return;
    if (rollbackReason.trim().length < 4) { notifyToast({ type: "error", message: "回滚原因至少填写 4 个字符" }); return; }
    if (!window.confirm(`确认将 ${configuration.name} 回滚到配置版本 ${revision.config_revision}？在线工具会在服务端重新执行健康检查。`)) return;
    setBusy(`rollback:${revision.id}`);
    try {
      const next = await api<ToolConfiguration>(`/api/admin/tools/${toolId}/configuration/rollback`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: configuration.config_revision, revision_id: revision.id, reason: rollbackReason.trim(), confirm: true }),
      });
      setConfiguration(next);
      setForm(formFrom(next));
      setRollbackReason("");
      setReason("");
      setConfirmSave(false);
      setDirty(false);
      setTestResult(null);
      setRevisions(await api<ToolConfigurationRevision[]>(`/api/admin/tools/${toolId}/configuration/revisions`));
      notifyToast({ type: "success", message: `已回滚到版本 ${revision.config_revision}`, details: `新配置版本：${next.config_revision}` });
    } catch (cause) {
      notifyToast({ type: "error", message: "配置回滚失败", details: errorMessage(cause) });
    } finally {
      setBusy("");
    }
  }

  async function toggleFileQuarantine() {
    if (!configuration || !form || busy) return;
    const nextQuarantined = !configuration.file_quarantined;
    const reason = window.prompt(nextQuarantined ? "请输入安全隔离原因（至少 4 个字符）：" : "请输入解除隔离原因（至少 4 个字符）：");
    if (reason === null) return;
    if (reason.trim().length < 4) { notifyToast({ type: "error", message: "原因至少 4 个字符" }); return; }
    setBusy("file-access");
    try {
      await api(`/api/admin/tools/${toolId}/file-access`, { method: "PATCH", body: JSON.stringify({ file_access_enabled: form.file_access_enabled, file_quarantined: nextQuarantined, reason: reason.trim() }) });
      await load();
      notifyToast({ type: "success", message: nextQuarantined ? "工具文件已安全隔离" : "工具文件隔离已解除" });
    } catch (cause) { notifyToast({ type: "error", message: "文件访问状态修改失败", details: errorMessage(cause) }); }
    finally { setBusy(""); }
  }

  async function removeStorageEndpoint(endpoint: StorageEndpoint) {
    if (endpoint.is_active || endpoint.file_count || endpoint.task_count || busy) return;
    const reason = window.prompt(`请输入移除历史存储端点 v${endpoint.revision} 的原因（至少 4 个字符）：`);
    if (reason === null) return;
    if (reason.trim().length < 4) { notifyToast({ type: "error", message: "原因至少 4 个字符" }); return; }
    setBusy(`remove-endpoint:${endpoint.id}`);
    try { await api(`/api/admin/tools/${toolId}/storage-endpoints/${endpoint.id}`, { method: "DELETE", body: JSON.stringify({ reason: reason.trim() }) }); await load(); notifyToast({ type: "success", message: "历史存储端点已移除" }); }
    catch (cause) { notifyToast({ type: "error", message: "历史存储端点移除失败", details: errorMessage(cause) }); }
    finally { setBusy(""); }
  }

  return (
    <PortalShell admin title="工具运行配置">
      <Link href={`/admin/tools/${toolId}`} className="back-link"><ArrowLeft size={15} />返回工具工作台</Link>
      <p className="page-intro">修改 Gateway 实际转发目标及工具级运行策略。工具 slug 和已发布接口路由不在此处修改。</p>
      {loading && <div className="state-box">正在加载工具配置...</div>}
      {error && <div className="state-box error" role="alert"><p>{error}</p><button className="button secondary compact" onClick={() => void load()}><RefreshCw size={15} />重试</button></div>}

      {!loading && configuration && form && (
        <>
          <div className="tool-config-meta">
            <div><span>工具</span><strong>{configuration.name}</strong><code>{configuration.slug}</code></div>
            <div><span>当前版本</span><strong>v{configuration.config_revision}</strong><small>{new Date(configuration.updated_at).toLocaleString()}</small></div>
            <div><span>生效状态</span><strong>{statusLabel(configuration.status)}</strong><small>{configuration.is_enabled ? "允许 Gateway 接收请求" : "Gateway 已停止接收请求"}</small></div>
          </div>

          <form onSubmit={save} className="tool-config-stack">
            <section className="panel settings-section">
              <div className="settings-section-heading"><div><h2><Server size={19} />基本信息与转发目标</h2><p>保存上游地址后，新进入的 Gateway 请求会使用新目标；进行中的请求不会被迁移。</p></div></div>
              <div className="settings-form-grid">
                <label className="form-field">工具名称<input value={form.name} onChange={(event) => update("name", event.target.value)} minLength={2} maxLength={128} required /></label>
                <label className="form-field">运行状态<select value={form.status} onChange={(event) => update("status", event.target.value as ConfigurationForm["status"])}><option value="draft">草稿</option><option value="active">已启用</option><option value="disabled">已停用</option></select><small>停用后所有已发布路由立即返回服务不可用。</small></label>
                <label className="form-field form-field-wide">工具说明<textarea value={form.description} onChange={(event) => update("description", event.target.value)} rows={3} maxLength={4000} /></label>
                <label className="form-field">上游根地址<input value={form.base_url} onChange={(event) => update("base_url", event.target.value)} type="url" inputMode="url" maxLength={512} placeholder="http://10.0.0.8:8000" required /><small>必须包含协议，可包含端口，不能包含路径。主机需命中部署白名单。</small></label>
                <label className="form-field">健康检查路径<input value={form.health_path} onChange={(event) => update("health_path", event.target.value)} maxLength={128} placeholder="/health" required /></label>
                <label className="form-field">默认网关前缀<input value={form.default_gateway_prefix} onChange={(event) => update("default_gateway_prefix", event.target.value)} maxLength={128} placeholder="留空表示根路径" /><small>仅用于后续接口导入映射，不会改写已经发布的路由。</small></label>
                <label className="form-field">网络区域<input value={form.network_zone} onChange={(event) => update("network_zone", event.target.value)} maxLength={64} required placeholder="local / private-vpc" /></label>
              </div>
              <div className="settings-boundary-list"><strong>允许的上游主机</strong><span>{allowedHosts.length ? allowedHosts.join("、") : "部署环境尚未配置 ALLOWED_UPSTREAM_HOSTS"}</span><small>这是服务端 SSRF 安全边界，只能通过部署环境修改，平台页面不会自动扩大白名单。</small></div>
            </section>

            <section className="panel settings-section">
              <div className="settings-section-heading"><div><h2><KeyRound size={19} />服务端认证</h2><p>这是 Gateway 到工具服务器的机器凭据，与用户全局 API Key 完全不同。</p></div><span className={configuration.token_configured ? "status good" : "status warn"}>{configuration.token_configured ? "密钥已配置" : "未配置密钥"}</span></div>
              <div className="settings-form-grid">
                <label className="form-field">认证方式<select value={form.auth_type} onChange={(event) => update("auth_type", event.target.value as ConfigurationForm["auth_type"])}><option value="none">无需认证</option><option value="bearer">Bearer Token</option><option value="header_api_key">Header API Key</option></select></label>
                <label className="form-field">认证请求头<input value={form.token_label} onChange={(event) => update("token_label", event.target.value)} disabled={form.auth_type !== "header_api_key"} maxLength={128} placeholder="X-Upstream-API-Key" required={form.auth_type === "header_api_key"} /></label>
                <label className="form-field form-field-wide">替换上游密钥<input value={form.upstream_token} onChange={(event) => update("upstream_token", event.target.value)} type="password" autoComplete="new-password" maxLength={2048} disabled={form.auth_type === "none" || form.clear_upstream_token} placeholder={configuration.token_configured ? "留空保留当前密钥" : "输入上游密钥"} /><small>密钥不会从接口回显。填写新值会替换旧值；选择无需认证会清除已保存密钥。</small></label>
              </div>
              {form.auth_type !== "none" && configuration.token_configured && <label className="settings-toggle-row"><span><strong>清除已保存密钥</strong><small>清除后当前认证模式将无法保存，除非同时改为“无需认证”。</small></span><input type="checkbox" checked={form.clear_upstream_token} onChange={(event) => update("clear_upstream_token", event.target.checked)} /></label>}
            </section>

            <section className="panel settings-section">
              <div className="settings-section-heading"><div><h2><Gauge size={19} />流量与超时</h2><p>工具级限流按“用户 API Key + 工具 + 固定一分钟窗口”独立计算，不会与其他工具相互占用。</p></div></div>
              <div className="settings-form-grid">
                <label className="form-field">每分钟请求上限<input value={form.rate_limit_per_minute} onChange={(event) => update("rate_limit_per_minute", Number(event.target.value))} type="number" min={1} max={10000} step={1} required /></label>
                <label className="form-field">失败重试次数<input value={form.retry_count} onChange={(event) => update("retry_count", Number(event.target.value))} type="number" min={0} max={5} step={1} required /><small>仅允许路由策略明确可重试时使用。</small></label>
                <label className="form-field">连接超时（秒）<input value={form.connect_timeout_seconds} onChange={(event) => update("connect_timeout_seconds", Number(event.target.value))} type="number" min={1} max={60} step={1} required /></label>
                <label className="form-field">请求超时（秒）<input value={form.request_timeout_seconds} onChange={(event) => update("request_timeout_seconds", Number(event.target.value))} type="number" min={1} max={600} step={1} required /></label>
              </div>
              <label className="settings-toggle-row"><span><strong>校验上游 TLS 证书</strong><small>HTTPS 上游应保持开启。关闭会降低链路身份校验能力，并被写入审计。</small></span><input type="checkbox" checked={form.verify_tls} onChange={(event) => update("verify_tls", event.target.checked)} /></label>
            </section>

            <section className="panel settings-section">
              <div className="settings-section-heading"><div><h2><Network size={19} />网络隔离声明</h2><p>记录工具服务器与 Gateway 之间实际采用的隔离方式，供发布和审计核对。</p></div></div>
              <div className="settings-form-grid">
                <label className="form-field">隔离方式<select value={form.network_isolation_mode} onChange={(event) => update("network_isolation_mode", event.target.value as ConfigurationForm["network_isolation_mode"])}><option value="firewall_allowlist">防火墙白名单</option><option value="private_network">私有网络</option><option value="machine_credential_only">仅机器凭据</option></select></label>
                <label className="form-field form-field-wide">隔离说明<textarea value={form.network_isolation_note} onChange={(event) => update("network_isolation_note", event.target.value)} rows={3} maxLength={1000} placeholder="说明安全组、VPC 或防火墙边界" /></label>
              </div>
              <label className="settings-toggle-row"><span><strong>确认当前隔离声明真实有效</strong><small>勾选人和时间会记录在工具配置及审计日志中。</small></span><input type="checkbox" checked={form.network_isolation_confirmed} onChange={(event) => update("network_isolation_confirmed", event.target.checked)} /></label>
            </section>

            <section className="panel settings-section">
              <div className="settings-section-heading"><div><h2><HardDrive size={19}/>文件与制品策略</h2><p>配置该工具的保留、容量和下载边界。填写 0 表示继承平台默认或平台硬上限。</p></div><span className={form.file_access_enabled?"status good":"status warn"}>{form.file_access_enabled?"历史文件可访问":"历史文件访问已关闭"}</span></div>
              <div className="settings-form-grid"><label className="form-field">保留期（天）<input value={form.file_retention_days} onChange={event=>update("file_retention_days",Number(event.target.value))} type="number" min={0} max={3650} step={1}/><small>0 表示继承平台默认；上游更短期限优先。</small></label><label className="form-field">工具容量限制（GiB）<input value={form.storage_quota_bytes/1_073_741_824} onChange={event=>update("storage_quota_bytes",Math.round(Number(event.target.value)*1_073_741_824))} type="number" min={0} max={1048576} step={1}/><small>0 表示不增加工具级逻辑容量限制。</small></label><label className="form-field">单文件上限（GiB）<input value={form.max_file_bytes/1_073_741_824} onChange={event=>update("max_file_bytes",Math.round(Number(event.target.value)*1_073_741_824))} type="number" min={0} max={50} step={1}/><small>0 表示使用平台 50 GiB 硬上限。</small></label></div>
              <label className="settings-toggle-row"><span><strong>允许访问历史文件</strong><small>关闭只阻止下载，不影响新 Gateway 调用；安全事故应同时在文件视图执行隔离。</small></span><input type="checkbox" checked={form.file_access_enabled} onChange={event=>update("file_access_enabled",event.target.checked)}/></label>
              <label className="settings-toggle-row"><span><strong>确认无法上报存储水位的风险</strong><small>仅在旧工具确实无法提供容量接口时勾选，否则应先配置 v2 存储水位能力。</small></span><input type="checkbox" checked={form.storage_risk_acknowledged} onChange={event=>update("storage_risk_acknowledged",event.target.checked)}/></label>
              {configuration.file_quarantined&&<div className="state-box error">该工具当前处于文件安全隔离状态，用户和管理员均不能下载历史文件。</div>}
              <div className="settings-actions"><span>安全隔离独立于工具启停和历史文件访问开关。</span><button className={configuration.file_quarantined?"button secondary":"button danger"} type="button" disabled={Boolean(busy)} onClick={()=>void toggleFileQuarantine()}><ShieldAlert size={15}/>{busy==="file-access"?"处理中":configuration.file_quarantined?"解除隔离":"安全隔离全部文件"}</button></div>
            </section>

            <section className="panel tool-config-confirm">
              <div><ShieldAlert size={22} /><span><strong>变更确认</strong><small>在线工具的转发、认证或 TLS 发生变化时，保存接口会再次测试候选上游；测试失败不会修改当前线上配置。</small></span></div>
              <label className="form-field">变更原因<input value={reason} onChange={(event) => { setReason(event.target.value); setConfirmSave(false); }} minLength={4} maxLength={500} placeholder="例如：迁移到新的生产实例" required /></label>
              <label className="tool-config-check"><input type="checkbox" checked={confirmSave} onChange={(event) => setConfirmSave(event.target.checked)} /><span>我已确认地址、端口、认证、限流和超时配置，并了解新请求会立即使用新配置。</span></label>
              {testResult && <div className={testResult.health.reachable ? "tool-config-test-result success" : "tool-config-test-result error"}><strong>最近一次候选测试：{testResult.health.reachable ? "可达" : "不可达"}</strong><p>状态码 {testResult.health.status_code ?? "无响应"}；变化：{testResult.changed_fields.map((item) => fieldLabels[item] || item).join("、") || "无"}</p></div>}
              <div className="settings-actions"><span>{dirty ? "存在尚未保存的修改" : "当前表单与已保存配置一致"}</span><div className="toolbar-actions"><button className="button secondary" type="button" onClick={() => void testCandidate()} disabled={Boolean(busy)}><PlugZap size={15} />{busy === "test" ? "测试中" : "测试候选配置"}</button><button className="button" type="submit" disabled={Boolean(busy) || !dirty}><Save size={15} />{busy === "save" ? "保存中" : "确认并保存"}</button></div></div>
            </section>
          </form>

          <section className="panel settings-section tool-config-history">
            <div className="settings-section-heading"><div><h2><RotateCcw size={19} />配置历史与回滚</h2><p>每次保存前保留一个加密快照。回滚会生成新版本，不会删除后续审计记录。</p></div></div>
            <div className="settings-form-grid"><label className="form-field form-field-wide">回滚原因<input value={rollbackReason} onChange={(event) => setRollbackReason(event.target.value)} minLength={4} maxLength={500} placeholder="说明为什么需要恢复历史配置" /></label></div>
            <div className="table-wrap"><table className="data-table"><thead><tr><th>历史版本</th><th>当时状态</th><th>上游</th><th>保存原因</th><th>时间</th><th>操作</th></tr></thead><tbody>{revisions.map((revision) => <tr key={revision.id}><td>v{revision.config_revision}</td><td>{statusLabel(revision.status)}</td><td><code>{revision.base_url_masked}</code></td><td>{revision.reason || "未记录"}</td><td>{new Date(revision.created_at).toLocaleString()}</td><td><button className="button secondary compact" type="button" disabled={Boolean(busy) || dirty} onClick={() => void rollback(revision)}><RotateCcw size={14} />{busy === `rollback:${revision.id}` ? "回滚中" : "回滚"}</button></td></tr>)}{!revisions.length && <tr><td colSpan={6}><div className="empty-inline">尚无历史版本。首次保存配置后会生成变更前快照。</div></td></tr>}</tbody></table></div>
            {dirty && <div className="settings-actions"><span>请先保存或刷新当前未保存修改，再执行回滚。</span></div>}
          </section>
          <section className="panel settings-section"><div className="settings-section-heading"><div><h2><Server size={19}/>历史存储端点</h2><p>历史任务和文件固定绑定创建时的端点版本。存在任一引用时，旧端点不能移除。</p></div></div><div className="table-wrap"><table className="data-table"><thead><tr><th>版本</th><th>脱敏地址</th><th>状态</th><th>关联资源</th><th>创建 / 停用</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{storageEndpoints.map(item=><tr key={item.id}><td>v{item.revision}</td><td><code>{item.base_url_masked}</code></td><td><span className={item.is_active?"status good":"status warn"}>{item.is_active?"当前":"历史"}</span><small>{item.token_configured?"凭据已保存":"无凭据"}</small></td><td>{item.task_count} 个任务 / {item.file_count} 个文件<small>待删除 {item.pending_delete_count}</small></td><td>{new Date(item.created_at).toLocaleString()}<small>{item.retired_at?new Date(item.retired_at).toLocaleString():"仍在使用"}</small></td><td><button className="button secondary compact" type="button" disabled={item.is_active||item.file_count>0||item.task_count>0||Boolean(busy)} onClick={()=>void removeStorageEndpoint(item)}>{busy===`remove-endpoint:${item.id}`?"移除中":"移除"}</button></td></tr>)}{!storageEndpoints.length&&<tr><td colSpan={6}><div className="state-box">尚无存储端点快照；首次登记任务或文件时会自动创建。</div></td></tr>}</tbody></table></div></section>
        </>
      )}
    </PortalShell>
  );
}
