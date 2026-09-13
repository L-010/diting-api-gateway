"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, KeyRound, Search } from "lucide-react";
import { PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { DeveloperApiDocs } from "@/lib/developer-docs";

function methodSummary(methods: string[]) {
  const ordered = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"];
  return ordered.filter((method) => methods.includes(method)).join(" / ") || "暂无方法";
}

export default function ToolDirectoryPage() {
  const [docs, setDocs] = useState<DeveloperApiDocs | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<DeveloperApiDocs>("/api/me/api-docs")
      .then(setDocs)
      .catch((cause) => setError(errorMessage(cause)));
  }, []);

  const tools = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (docs?.tools || []).filter((tool) => {
      if (!normalized) return true;
      return [tool.name, tool.slug, tool.description, ...tool.endpoints.flatMap((endpoint) => [endpoint.summary, endpoint.description, endpoint.gateway_path])]
        .join(" ")
        .toLowerCase()
        .includes(normalized);
    });
  }, [docs, query]);

  return (
    <PortalShell title="工具与接口">
      <p className="page-intro">
        这里仅展示已上线并对当前账号开放的工具。先选择工具，再在工具详情中查看接口用途、参数和可直接复制的调用示例。
      </p>
      {error && <div className="state-box error">{error}</div>}
      {!docs && !error && <div className="state-box">正在加载可用工具...</div>}

      {docs && (
        <>
          <div className="directory-toolbar">
            <label className="directory-search">
              <Search size={17} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具、接口名称或 Gateway 路径" />
            </label>
            <div className="directory-summary">
              <strong>{tools.length}</strong> 个工具 · <strong>{tools.reduce((total, tool) => total + tool.endpoints.length, 0)}</strong> 个接口
            </div>
          </div>

          <div className="tool-directory-grid">
            {tools.map((tool) => {
              const methods = [...new Set(tool.endpoints.map((endpoint) => endpoint.method))];
              const ownerCount = tool.endpoints.filter((endpoint) => endpoint.route_policy.access_mode === "owner").length;
              return (
                <article className="tool-directory-card" key={tool.id}>
                  <div className="tool-directory-card-head">
                    <span className="tag">{tool.slug}</span>
                    <span className="status good">已上线</span>
                  </div>
                  <h2>{tool.name}</h2>
                  <p>{tool.description || "管理员尚未填写工具说明。"}</p>
                  <dl className="tool-directory-meta">
                    <div><dt>可用接口</dt><dd>{tool.endpoints.length}</dd></div>
                    <div><dt>请求方法</dt><dd>{methodSummary(methods)}</dd></div>
                    <div><dt>资源隔离</dt><dd>{ownerCount ? `${ownerCount} 个创建者接口` : "无用户资源接口"}</dd></div>
                    <div><dt>Gateway 前缀</dt><dd><code>{tool.gateway_base_path}</code></dd></div>
                  </dl>
                  <Link className="button" href={`/tools/${tool.slug}`}>
                    查看接口与调用示例<ArrowRight size={16} />
                  </Link>
                </article>
              );
            })}
          </div>

          {!tools.length && (
            <div className="state-box">
              <p>{query ? "没有匹配的工具或接口，请调整搜索条件。" : "管理员尚未发布普通用户可调用的工具。"}</p>
            </div>
          )}

          <div className="directory-footnote">
            <KeyRound size={17} />
            <span>所有工具使用同一个平台 API 密钥；工具详情不会展示真实上游服务器地址或机器凭证。</span>
            <Link href="/api-keys">管理 API 密钥</Link>
          </div>
        </>
      )}
    </PortalShell>
  );
}
