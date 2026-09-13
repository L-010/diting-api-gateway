"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Copy, KeyRound, Search } from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { copyToClipboard } from "@/lib/clipboard";
import {
  DeveloperApiDocs,
  DeveloperEndpointDoc,
  buildCurl,
  buildJavaScript,
  buildPython,
  endpointContentTypes,
  endpointUrl,
  formatBytes,
  methodClass,
  parameterRows,
  requestExample,
  responseExample,
} from "@/lib/developer-docs";

type Quickstart = { active_key_count: number };
type CodeLanguage = "curl" | "python" | "javascript";

const accessModeLabels: Record<string, string> = {
  authenticated: "平台用户可调用",
  owner: "仅可操作自己创建的资源",
  shared: "平台用户共享数据",
  admin_only: "仅管理员",
};

function stringifyExample(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function resourceRuleSummary(endpoint: DeveloperEndpointDoc) {
  const policy = endpoint.route_policy.resource_policy;
  const preChecks = Array.isArray(policy.pre_checks) ? policy.pre_checks : [];
  const postActions = Array.isArray(policy.post_actions) ? policy.post_actions : [];
  const listFilter = policy.list_filter && typeof policy.list_filter === "object" ? [policy.list_filter] : [];
  const rules = [...preChecks, ...postActions, ...listFilter] as Array<Record<string, unknown>>;
  if (!rules.length) return "该接口不需要资源归属参数";
  return rules.map((rule) => {
    const kind = String(rule.resource_kind || "resource");
    const selector = String(rule.selector || rule.id_selector || (Array.isArray(rule.selectors) ? rule.selectors.join(" / ") : ""));
    return selector ? `${kind}：${selector}` : kind;
  }).join("；");
}

function EndpointDetails({ endpoint, baseUrl, authHeader, errorCodes }: { endpoint: DeveloperEndpointDoc; baseUrl: string; authHeader: string; errorCodes: DeveloperApiDocs["error_codes"] }) {
  const [language, setLanguage] = useState<CodeLanguage>("curl");
  const params = parameterRows(endpoint);
  const request = requestExample(endpoint);
  const response = responseExample(endpoint);
  const contentTypes = endpointContentTypes(endpoint);
  const examples = {
    curl: buildCurl(baseUrl, authHeader, endpoint),
    python: buildPython(baseUrl, authHeader, endpoint),
    javascript: buildJavaScript(baseUrl, authHeader, endpoint),
  };
  const url = endpointUrl(baseUrl, endpoint);

  return (
    <div className="endpoint-doc-detail">
      <div className="toolbar endpoint-doc-heading">
        <div>
          <div><span className={methodClass(endpoint.method)}>{endpoint.method}</span><span className="endpoint-operation">{endpoint.operation_id || "未提供 operationId"}</span></div>
          <h2>{endpoint.summary || "未命名接口"}</h2>
          <p>{endpoint.description || "管理员尚未提供该接口的用途说明。"}</p>
        </div>
        <button className="button secondary compact" onClick={() => void copyToClipboard(url, "Gateway 地址已复制")}><Copy size={14} />复制地址</button>
      </div>

      <div className="gateway-address"><code>{url}</code></div>
      {/[{][^}]+[}]/.test(endpoint.gateway_path) && <p className="muted">调用前请将路径中的占位参数替换为真实资源 ID。</p>}

      <div className="doc-facts">
        <div><span>访问范围</span><strong>{accessModeLabels[endpoint.route_policy.access_mode] || endpoint.route_policy.access_mode}</strong></div>
        <div><span>资源规则</span><strong>{resourceRuleSummary(endpoint)}</strong></div>
        <div><span>风险 / 路由版本</span><strong>{endpoint.route_policy.risk_level || "未标记"} / v{endpoint.route_policy.route_version}</strong></div>
        <div><span>幂等要求</span><strong>{endpoint.route_policy.require_idempotency_key ? "必须提供 Idempotency-Key" : "不强制"}</strong></div>
        <div><span>重试策略</span><strong>{endpoint.route_policy.allow_retry ? "允许按退避策略重试" : "禁止自动重试"}</strong></div>
        <div><span>工具限流</span><strong>{endpoint.route_policy.rate_limit_per_minute ? `${endpoint.route_policy.rate_limit_per_minute} 次/分钟` : "以服务响应为准"}</strong></div>
        <div><span>请求限制</span><strong>{formatBytes(endpoint.route_policy.max_request_bytes)} / {endpoint.route_policy.request_timeout_seconds}s</strong></div>
        <div><span>响应限制</span><strong>{formatBytes(endpoint.route_policy.max_response_bytes)}</strong></div>
      </div>

      <section className="doc-section">
        <h3>请求参数</h3>
        <div className="table-wrap">
          <table className="data-table doc-table">
            <thead><tr><th>参数</th><th>位置</th><th>必填</th><th>类型</th><th>说明</th></tr></thead>
            <tbody>
              {params.map((param) => (
                <tr key={`${param.location}:${param.name}`}><td><code>{param.name}</code></td><td>{param.location || "-"}</td><td>{param.required ? "是" : "否"}</td><td>{param.type || "-"}</td><td>{param.description || "-"}</td></tr>
              ))}
              {!params.length && <tr><td colSpan={5}><div className="empty-inline">该接口没有声明 path 或 query 参数。</div></td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <div className="doc-payload-grid">
        <section className="doc-section">
          <h3>请求体</h3>
          <p className="muted">Content-Type：{contentTypes.length ? contentTypes.join(", ") : "无请求体"}</p>
          <pre className="code-block">{request === null ? "该接口无请求体或规范未提供示例。" : stringifyExample(request)}</pre>
        </section>
        <section className="doc-section">
          <h3>响应示例</h3>
          <p className="muted">响应头会返回 <code>x-request-id</code>，请在业务日志中保留。</p>
          <pre className="code-block">{stringifyExample(response)}</pre>
        </section>
      </div>

      <section className="doc-section">
        <div className="toolbar">
          <div><h3>调用示例</h3><p className="muted">示例中的 <code>agw_xxx</code> 需要替换为自己的完整 API 密钥。</p></div>
          <button className="button secondary compact" onClick={() => void copyToClipboard(examples[language], `${language} 示例已复制`)}><Copy size={14} />复制示例</button>
        </div>
        <div className="segmented-control" aria-label="代码语言">
          {(["curl", "python", "javascript"] as CodeLanguage[]).map((item) => <button className={language === item ? "active" : ""} key={item} onClick={() => setLanguage(item)}>{item === "javascript" ? "JavaScript" : item === "python" ? "Python" : "curl"}</button>)}
        </div>
        <pre className="code-block code-example">{examples[language]}</pre>
      </section>

      <div className="endpoint-errors"><strong>平台错误码</strong>{errorCodes.map((item) => <span key={`${item.http_status}-${item.code}`}><code>{item.http_status} {item.code}</code> {item.message}</span>)}</div>
    </div>
  );
}

export default function ToolDetailPage() {
  const params = useParams<{ toolSlug: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [docs, setDocs] = useState<DeveloperApiDocs | null>(null);
  const [activeKeyCount, setActiveKeyCount] = useState(0);
  const [query, setQuery] = useState("");
  const [method, setMethod] = useState("ALL");
  const [error, setError] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  useEffect(() => {
    Promise.all([api<DeveloperApiDocs>("/api/me/api-docs"), api<Quickstart>("/api/me/quickstart")])
      .then(([docsPayload, quickstart]) => {
        setDocs(docsPayload);
        setBaseUrl((docsPayload.gateway_base_url || window.location.origin).replace(/\/$/, ""));
        setActiveKeyCount(quickstart.active_key_count);
      })
      .catch((cause) => setError(errorMessage(cause)));
  }, []);

  const tool = docs?.tools.find((item) => item.slug === params.toolSlug);
  const methods = useMemo(() => tool ? [...new Set(tool.endpoints.map((endpoint) => endpoint.method))] : [], [tool]);
  const visibleEndpoints = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (tool?.endpoints || []).filter((endpoint) => {
      const methodMatch = method === "ALL" || endpoint.method === method;
      const queryMatch = !normalized || [endpoint.summary, endpoint.description, endpoint.gateway_path, endpoint.operation_id || "", endpoint.method].join(" ").toLowerCase().includes(normalized);
      return methodMatch && queryMatch;
    });
  }, [method, query, tool]);
  const requestedEndpointId = searchParams.get("endpoint");
  const selectedEndpoint = visibleEndpoints.find((endpoint) => endpoint.id === requestedEndpointId) || visibleEndpoints[0] || null;

  function selectEndpoint(endpoint: DeveloperEndpointDoc) {
    router.replace(`/tools/${params.toolSlug}?endpoint=${encodeURIComponent(endpoint.id)}`, { scroll: false });
  }

  return (
    <PortalShell title={tool?.name || "工具详情"}>
      <Link href="/tools" className="back-link"><ArrowLeft size={15} />返回工具目录</Link>
      {error && <div className="state-box error">{error}</div>}
      {!docs && !error && <div className="state-box">正在加载工具接口...</div>}
      {docs && !tool && <div className="state-box error">该工具不存在、已停用，或没有对当前账号开放的接口。</div>}

      {tool && (
        <>
          <div className="tool-doc-summary">
            <div><span className="tag">{tool.slug}</span><p>{tool.description || "管理员尚未填写工具说明。"}</p></div>
            <div><strong>{tool.endpoints.length}</strong><span>可用接口</span></div>
            <div><code>{tool.gateway_base_path}</code><span>Gateway 前缀</span></div>
          </div>

          {!activeKeyCount && <div className="key-warning"><KeyRound size={19} /><span>你还没有有效 API 密钥。可以先浏览文档，但调用会返回 401。</span><Link href="/api-keys">创建密钥</Link></div>}

          <div className="tool-doc-layout">
            <aside className="endpoint-index-panel">
              <div className="endpoint-index-title"><strong>接口列表</strong><span>{visibleEndpoints.length} / {tool.endpoints.length}</span></div>
              <label className="directory-search compact-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索接口" /></label>
              <div className="method-filter" aria-label="请求方法筛选">
                <button className={method === "ALL" ? "active" : ""} onClick={() => setMethod("ALL")}>全部</button>
                {methods.map((item) => <button className={method === item ? "active" : ""} key={item} onClick={() => setMethod(item)}>{item}</button>)}
              </div>
              <div className="endpoint-nav-list">
                {visibleEndpoints.map((endpoint) => (
                  <button className={`endpoint-nav-item ${selectedEndpoint?.id === endpoint.id ? "active" : ""}`} key={endpoint.id} onClick={() => selectEndpoint(endpoint)}>
                    <span className={methodClass(endpoint.method)}>{endpoint.method}</span>
                    <span><strong>{endpoint.summary || "未命名接口"}</strong><code>{endpoint.gateway_path}</code></span>
                  </button>
                ))}
                {!visibleEndpoints.length && <div className="empty-inline">没有匹配的接口。</div>}
              </div>
            </aside>

            <section className="endpoint-detail-panel" aria-label="接口文档详情">
              {selectedEndpoint && baseUrl ? <EndpointDetails endpoint={selectedEndpoint} baseUrl={baseUrl} authHeader={docs?.auth_header || "X-API-Key"} errorCodes={docs?.error_codes || []} key={selectedEndpoint.id} /> : <div className="state-box">{selectedEndpoint ? "正在生成当前访问地址的调用示例..." : "请调整筛选条件后选择接口。"}</div>}
            </section>
          </div>
        </>
      )}
    </PortalShell>
  );
}
