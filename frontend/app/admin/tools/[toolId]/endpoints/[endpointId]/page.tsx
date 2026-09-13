"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, Save, ShieldAlert } from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { EndpointView, ToolView, accessModeLabel, riskLabel, statusLabel } from "@/lib/admin-tools";
import { DeveloperEndpointDoc, parameterRows, requestExample, responseExample } from "@/lib/developer-docs";
import { notifyToast } from "@/lib/toast";

function exampleText(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

export default function EndpointEditorPage() {
  const params = useParams<{ toolId: string; endpointId: string }>();
  const { toolId, endpointId } = params;
  const [tool, setTool] = useState<ToolView | null>(null);
  const [endpoint, setEndpoint] = useState<EndpointView | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [actionReason, setActionReason] = useState("");

  async function load() {
    const [detail, endpointDetail] = await Promise.all([
      api<ToolView>(`/api/admin/tools/${toolId}`),
      api<EndpointView>(`/api/admin/endpoints/${endpointId}`),
    ]);
    setTool(detail);
    setEndpoint(endpointDetail);
  }

  useEffect(() => {
    load().catch((cause) => setError(errorMessage(cause)));
  }, [toolId, endpointId]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!endpoint) return;
    setMessage("");
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const contentTypes = String(form.get("content_types") || "")
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const riskFlags = String(form.get("risk_flags") || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      await api(`/api/admin/endpoints/${endpoint.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          gateway_path: form.get("gateway_path"),
          summary: form.get("summary"),
          public_description: form.get("public_description"),
          content_types: contentTypes,
          status: endpoint.status === "published" ? undefined : form.get("status"),
          exclusion_reason: form.get("exclusion_reason"),
          risk_level: form.get("risk_level"),
          risk_flags: riskFlags,
          storage_action: form.get("storage_action"),
          governance: {
            audit_required: form.get("audit_required") === "on",
            timeout_seconds: Number(form.get("timeout_seconds") || 60),
            rate_limit_per_minute: Number(form.get("rate_limit_per_minute") || tool?.rate_limit_per_minute || 60),
          },
          reason: form.get("reason"),
        }),
      });
      const message = "接口治理信息已保存。";
      setMessage(message);
      notifyToast({ type: "success", message });
      setActionReason("");
      await load();
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function publish(isEnabled: boolean) {
    if (!endpoint) return;
    const reason = actionReason.trim();
    if (reason.length < 4) {
      const message = "发布或停用前，请填写至少 4 个字符的本次修改原因。";
      setError(message);
      notifyToast({ type: "error", message });
      return;
    }
    const action = isEnabled ? "发布" : "停用";
    if (!window.confirm(`${action}接口“${endpoint.summary || endpoint.operation_id || endpoint.upstream_path}”？${isEnabled ? "发布后普通用户可能立即调用。" : "停用后依赖该路由的调用会立即失败。"}`)) return;
    setMessage("");
    setError("");
    try {
      await api(`/api/admin/endpoints/${endpoint.id}/publish`, {
        method: "PATCH",
        body: JSON.stringify({
          is_enabled: isEnabled,
          confirm: isEnabled,
          reason,
        }),
      });
      const message = isEnabled ? "接口已发布。" : "接口已停用。";
      setMessage(message);
      notifyToast({ type: "success", message });
      setActionReason("");
      await load();
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function setGovernanceStatus(action: "exclude" | "block") {
    if (!endpoint) return;
    const label = action === "exclude" ? "排除" : "阻断";
    const reason = actionReason.trim();
    if (reason.length < 4) {
      const message = `${label}接口前，请填写至少 4 个字符的本次修改原因。`;
      setError(message);
      notifyToast({ type: "error", message });
      return;
    }
    setMessage("");
    setError("");
    try {
      await api<EndpointView>(`/api/admin/endpoints/${endpoint.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      const message = `接口已${label}。`;
      setMessage(message);
      notifyToast({ type: "success", message });
      setActionReason("");
      await load();
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  if (!tool || !endpoint) {
    return (
      <PortalShell admin title="接口结构化精修">
        <div className="state-box">正在加载接口...</div>
      </PortalShell>
    );
  }

  const governanceJson = JSON.stringify(endpoint.governance, null, 2);
  const requestJson = JSON.stringify(endpoint.request_body, null, 2);
  const responsesJson = JSON.stringify(endpoint.responses, null, 2);
  const routePolicyJson = endpoint.route_policy ? JSON.stringify(endpoint.route_policy, null, 2) : "尚未发布，暂无执行策略快照。";
  const timeoutValue = Number(endpoint.governance.timeout_seconds || 60);
  const rateValue = Number(endpoint.governance.rate_limit_per_minute || tool.rate_limit_per_minute);
  const docEndpoint: DeveloperEndpointDoc = {
    id: endpoint.id,
    tool_slug: tool.slug,
    tool_name: tool.name,
    method: endpoint.method,
    gateway_path: endpoint.gateway_path || endpoint.upstream_path,
    operation_id: endpoint.operation_id,
    summary: endpoint.summary,
    description: endpoint.public_description,
    content_types: endpoint.content_types,
    parameters: endpoint.parameters,
    request_body: endpoint.request_body,
    responses: endpoint.responses,
    route_policy: endpoint.route_policy
      ? {
          route_version: endpoint.route_policy.route_version,
          allowed_content_types: endpoint.route_policy.allowed_content_types,
          max_request_bytes: endpoint.route_policy.max_request_bytes,
          max_response_bytes: endpoint.route_policy.max_response_bytes,
          request_timeout_seconds: endpoint.route_policy.request_timeout_seconds,
          allow_stream_upload: endpoint.route_policy.allow_stream_upload,
          allow_stream_download: endpoint.route_policy.allow_stream_download,
          access_mode: endpoint.route_policy.access_mode,
          resource_policy: endpoint.route_policy.resource_policy,
          policy_version: endpoint.route_policy.policy_version,
          require_idempotency_key: endpoint.route_policy.require_idempotency_key,
          allow_retry: endpoint.route_policy.allow_retry,
          risk_level: endpoint.route_policy.risk_level,
        }
      : {
          route_version: 0,
          allowed_content_types: endpoint.content_types,
          max_request_bytes: 100 * 1024 * 1024,
          max_response_bytes: 1024 * 1024 * 1024,
          request_timeout_seconds: timeoutValue,
          allow_stream_upload: false,
          allow_stream_download: false,
          access_mode: endpoint.access_policy.access_mode,
          resource_policy: endpoint.access_policy.rules,
          policy_version: 0,
          require_idempotency_key: false,
          allow_retry: false,
          risk_level: endpoint.risk_level,
        },
  };
  const fieldRows = parameterRows(docEndpoint);
  const requestExampleValue = requestExample(docEndpoint);
  const responseExampleValue = responseExample(docEndpoint);

  return (
    <PortalShell admin title="单接口结构化精修">
      <p className="page-intro">
        这里用于发布前最后治理：路由映射、参数/请求体/响应摘要、Content-Type、限流超时和风险标记；所有状态操作都会记录管理员填写的审计原因。
      </p>

      <div className="toolbar">
        <div>
          <span className="status info">{endpoint.method}</span>
          <strong style={{ marginLeft: 10 }}>{endpoint.summary || endpoint.operation_id || "未命名接口"}</strong>
        </div>
        <Link href={`/admin/tools/${tool.id}`} className="icon-text-button">
          返回工具工作台
        </Link>
      </div>

      {(message || error) && <p className={`form-message ${message ? "success" : ""}`}>{message || error}</p>}

      <div className="page-split editor-layout">
        <form className="panel" onSubmit={save}>
          <div className="toolbar">
            <h2>结构化编辑</h2>
            <span className={endpoint.status === "published" ? "status good" : "status warn"}>{statusLabel(endpoint.status)}</span>
          </div>
          <div className="inline-form">
            <label className="form-field">
              Gateway 路径
              <input name="gateway_path" defaultValue={endpoint.gateway_path || endpoint.upstream_path} />
              <span className="muted">实际入口：/gateway/{tool.slug}{"{gateway_path}"}</span>
            </label>
            <label className="form-field">
              上游路径
              <input value={endpoint.upstream_path} readOnly />
            </label>
            <label className="form-field">
              接口摘要
              <input name="summary" defaultValue={endpoint.summary} />
            </label>
            <label className="form-field">
              状态
              <select name="status" defaultValue={endpoint.status} disabled={endpoint.status === "published"}>
                {endpoint.status === "published" && <option value="published">已发布</option>}
                <option value="candidate">候选</option>
                <option value="draft">草稿</option>
                <option value="excluded">排除</option>
                <option value="blocked">阻断</option>
              </select>
            </label>
            <label className="form-field full">
              对用户展示的说明
              <textarea name="public_description" defaultValue={endpoint.public_description || endpoint.summary} />
            </label>
            <label className="form-field">
              Content-Type（每行一个）
              <textarea name="content_types" defaultValue={endpoint.content_types.join("\n")} />
            </label>
            <label className="form-field">
              风险标记（逗号分隔）
              <textarea name="risk_flags" defaultValue={endpoint.risk_flags.join(", ")} />
            </label>
            <label className="form-field">
              风险等级
              <select name="risk_level" defaultValue={endpoint.risk_level}>
                <option value="info">信息</option>
                <option value="low">低</option>
                <option value="medium">中</option>
                <option value="high">高</option>
                <option value="blocker">阻断</option>
              </select>
            </label>
            <label className="form-field">
              排除/阻断原因
              <textarea name="exclusion_reason" defaultValue={endpoint.exclusion_reason} />
            </label>
            <label className="form-field">
              超时（秒）
              <input name="timeout_seconds" type="number" min={1} max={600} defaultValue={timeoutValue} />
            </label>
            <label className="form-field">
              限流（每分钟）
              <input name="rate_limit_per_minute" type="number" min={1} max={10000} defaultValue={rateValue} />
            </label>
            <label className="form-field">
              审计策略
              <span>
                <input name="audit_required" type="checkbox" defaultChecked={Boolean(endpoint.governance.audit_required)} style={{ marginRight: 8 }} />
                对该接口强制审计
              </span>
            </label>
            <label className="form-field">
              存储行为
              <select name="storage_action" defaultValue={endpoint.route_policy?.storage_action || String(endpoint.governance.storage_action || "none")}>
                <option value="none">不产生文件</option><option value="upload_file">上传文件</option><option value="create_task">创建产文件任务</option><option value="produce_files">同步产生文件</option><option value="delete_file">删除文件</option>
              </select>
              <span className="muted">只有上传或产文件行为会在 Gateway 调用前检查配额。</span>
            </label>
            <label className="form-field full">
              本次修改原因
              <input name="reason" value={actionReason} onChange={(event) => setActionReason(event.target.value)} placeholder="例如：确认该接口不暴露内部资源，允许进入灰度发布。" />
            </label>
          </div>

          <div className="toolbar-actions">
            <button className="button" type="submit">
              <Save size={16} />
              保存精修
            </button>
            <button
              className="button secondary"
              type="button"
              disabled={!endpoint.enabled && (endpoint.status === "excluded" || endpoint.status === "blocked" || endpoint.risk_level === "blocker")}
              title={!endpoint.enabled && (endpoint.status === "excluded" || endpoint.status === "blocked" || endpoint.risk_level === "blocker") ? "请先保存整改结果，将状态改为候选或草稿并降低阻断风险" : undefined}
              onClick={() => void publish(!endpoint.enabled)}
            >
              <CheckCircle2 size={16} />
              {endpoint.enabled ? "停用路由" : "发布路由"}
            </button>
            <button className="button secondary" type="button" onClick={() => void setGovernanceStatus("exclude")}>
              排除接口
            </button>
            <button className="button secondary" type="button" onClick={() => void setGovernanceStatus("block")}>
              阻断接口
            </button>
          </div>
        </form>

        <aside className="panel">
          <ShieldAlert size={25} color="#b76808" />
          <h2 style={{ marginTop: 12 }}>发布影响</h2>
          <div className="list-row">
            <span>风险等级</span>
            <span className={endpoint.risk_level === "high" || endpoint.risk_level === "blocker" ? "status warn" : "status good"}>{riskLabel(endpoint.risk_level)}</span>
          </div>
          <div className="list-row">
            <span>已发布</span>
            <strong>{endpoint.enabled ? "是" : "否"}</strong>
          </div>
          {endpoint.route_policy && (
            <>
              <div className="list-row">
                <span>路由版本</span>
                <strong>v{endpoint.route_policy.route_version}</strong>
              </div>
              <div className="list-row">
                <span>访问策略</span>
                <strong>{accessModeLabel(endpoint.route_policy.access_mode)}</strong>
              </div>
            </>
          )}
          <div className="signal-card info">
            <strong>Gateway</strong>
            <p className="endpoint-path">
              /gateway/{tool.slug}
              {endpoint.gateway_path || endpoint.upstream_path}
            </p>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>统一访问策略</strong>
            <p className="muted">
              模式：{accessModeLabel(endpoint.access_policy.access_mode)}
              <br />
              策略状态：{endpoint.access_policy.confirmed ? "已确认" : "待确认"}
              <br />
              规则完整：{endpoint.access_policy.complete ? "是" : "否"}
              <br />
              {endpoint.access_policy.reason || endpoint.access_policy.missing_fields.join("、") || "无缺失字段"}
            </p>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>执行策略快照</strong>
            <pre className="code-block">{routePolicyJson}</pre>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>参数与请求字段</strong>
            <div className="table-wrap" style={{ marginTop: 10 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>参数</th>
                    <th>位置</th>
                    <th>必填</th>
                    <th>类型</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {fieldRows.map((row) => (
                    <tr key={`${row.location}:${row.name}`}>
                      <td>
                        <code>{row.name}</code>
                      </td>
                      <td>{row.location || "-"}</td>
                      <td>{row.required ? "是" : "否"}</td>
                      <td>{row.type || "-"}</td>
                      <td>{row.description || "-"}</td>
                    </tr>
                  ))}
                  {!fieldRows.length && (
                    <tr>
                      <td colSpan={5}>该接口未声明参数或请求字段。</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>请求示例</strong>
            <pre className="code-block">{requestExampleValue === null ? "该接口无请求体或规范未提供请求示例。" : exampleText(requestExampleValue)}</pre>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>响应示例</strong>
            <pre className="code-block">{exampleText(responseExampleValue)}</pre>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>治理 JSON</strong>
            <pre className="code-block">{governanceJson}</pre>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>请求体摘要</strong>
            <pre className="code-block">{requestJson}</pre>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 14 }}>
            <strong>响应模型摘要</strong>
            <pre className="code-block">{responsesJson}</pre>
          </div>
        </aside>
      </div>
    </PortalShell>
  );
}
