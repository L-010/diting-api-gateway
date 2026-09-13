"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, FileJson, ShieldX, UploadCloud } from "lucide-react";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { ImportBatchView, OpenApiDiffView, diffLabel, riskLabel } from "@/lib/admin-tools";
import { notifyToast } from "@/lib/toast";

type ImportResult = { batch: ImportBatchView | null; diffs: OpenApiDiffView[] };

export default function ToolImportPage() {
  const params = useParams<{ toolId: string }>();
  const toolId = params.toolId;
  const [sourceType, setSourceType] = useState("upstream");
  const [batch, setBatch] = useState<ImportBatchView | null>(null);
  const [diffs, setDiffs] = useState<OpenApiDiffView[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loadingLatest, setLoadingLatest] = useState(true);
  const unresolvedBlockers = diffs.filter((item) => item.risk_level === "blocker" && item.decision === "pending");
  const pendingDecisionCount = diffs.filter((item) => item.decision === "pending").length;

  useEffect(() => {
    let cancelled = false;
    api<ImportResult>(`/api/admin/tools/${toolId}/import-batches/latest`)
      .then((result) => {
        if (cancelled || !result.batch) return;
        setBatch(result.batch);
        setDiffs(result.diffs);
      })
      .catch((cause) => {
        if (!cancelled) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setLoadingLatest(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toolId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      let result: ImportResult;
      if (sourceType === "upload") {
        const upload = new FormData();
        const file = form.get("file");
        if (!(file instanceof File) || !file.name) {
          setError("请选择 JSON/YAML 文件。");
          notifyToast({ type: "error", message: "请选择 JSON/YAML 文件" });
          return;
        }
        upload.set("file", file);
        result = await api<ImportResult>(`/api/admin/tools/${toolId}/openapi/upload`, { method: "POST", body: upload });
      } else {
        result = await api<ImportResult>(`/api/admin/tools/${toolId}/openapi/parse`, {
          method: "POST",
          body: JSON.stringify({
            source_type: sourceType,
            source_url: form.get("source_url") || "",
            document_text: form.get("document_text") || "",
          }),
        });
      }
      setBatch(result.batch);
      setDiffs(result.diffs);
      const message = "导入预览已生成。请检查阻断、排除和变更项，再确认批次。";
      setMessage(message);
      notifyToast({ type: "success", message });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function confirmBatch() {
    if (!batch) return;
    setMessage("");
    setError("");
    if (unresolvedBlockers.length) {
      const message = `仍有 ${unresolvedBlockers.length} 个阻断风险差异未决策，不能确认导入批次。`;
      setError(`${message}请先在下方表格中选择“阻断”或“排除”。`);
      notifyToast({ type: "error", message });
      return;
    }
    if (pendingDecisionCount) {
      const message = `仍有 ${pendingDecisionCount} 个接口未决策，不能确认导入批次。`;
      setError(message);
      notifyToast({ type: "error", message });
      return;
    }
    const reason = "管理员确认全部接口治理决策并应用导入批次";
    try {
      const confirmed = await api<ImportBatchView>(`/api/admin/import-batches/${batch.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, reason }),
      });
      setBatch(confirmed);
      const message = "导入批次已确认，可以一次性发布全部放行接口。";
      setMessage(message);
      notifyToast({ type: "success", message });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  async function decide(diff: OpenApiDiffView, decision: "accept" | "exclude" | "block") {
    setMessage("");
    setError("");
    const labels = { accept: "接受", exclude: "排除", block: "阻断" };
    try {
      const updated = await api<OpenApiDiffView>(`/api/admin/diffs/${diff.id}/decision`, {
        method: "PATCH",
        body: JSON.stringify({ decision }),
      });
      setDiffs((rows) => rows.map((item) => (item.id === updated.id ? updated : item)));
      const message = `已将 ${updated.method} ${updated.gateway_path} 标记为“${labels[decision]}”。`;
      setMessage(message);
      notifyToast({ type: "success", message });
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      notifyToast({ type: "error", message });
    }
  }

  return (
    <PortalShell admin title="OpenAPI / Swagger 导入预览">
      <p className="page-intro">
        支持 OpenAPI 3.0/3.1 与 Swagger 2.0；URL 来源仍受上游白名单限制；远程 $ref、未支持方法、非法路径与外部跳转会被拒绝。
      </p>

      <div className="page-split import-layout">
        <form className="panel" onSubmit={submit}>
          <div className="toolbar">
            <h2>API 来源</h2>
            <span className="status info">解析不发布</span>
          </div>
          <label className="form-field">
            导入方式
            <select value={sourceType} onChange={(event) => setSourceType(event.target.value)}>
              <option value="upstream">从已配置上游 /openapi.json 拉取</option>
              <option value="url">指定 OpenAPI/Swagger URL</option>
              <option value="upload">上传 JSON/YAML 文件</option>
              <option value="text">粘贴 JSON/YAML 文本</option>
            </select>
          </label>
          {sourceType === "url" && (
            <label className="form-field">
              文档 URL
              <input name="source_url" placeholder="http://127.0.0.1:18000/openapi.json" />
              <span className="muted">推荐填写 /openapi.json；若填写 FastAPI/Swagger UI 的 /docs 或 /redoc，平台会尝试自动发现同源规范地址。不允许 query、fragment、凭据和白名单外主机。</span>
            </label>
          )}
          {sourceType === "upload" && (
            <label className="upload-zone">
              <UploadCloud size={22} />
              <strong>上传规范文件</strong>
              <input name="file" type="file" accept=".json,.yaml,.yml,application/json,text/yaml" />
              <span className="muted">最大 2 MiB；文件内容不会被原样回显。</span>
            </label>
          )}
          {sourceType === "text" && (
            <label className="form-field">
              文档文本
              <textarea name="document_text" rows={14} placeholder="粘贴 OpenAPI/Swagger JSON 或 YAML" />
            </label>
          )}
          <p className={`form-message ${message ? "success" : ""}`}>{message || error}</p>
          <div className="toolbar-actions">
            <button className="button" type="submit">
              <FileJson size={16} />
              解析并生成预览
            </button>
            <Link href={`/admin/tools/${toolId}`} className="button secondary">
              返回工具工作台
            </Link>
          </div>
          <p className="notice" style={{ marginTop: 16 }}>
            默认排除 internal/admin/private/debug/deprecated 接口；路径冲突和上游删除会阻断发布。
          </p>
        </form>

        <section>
          {batch ? (
            <>
              <div className="metrics">
                <Metric label="接口总数" value={String(batch.total_operations)} caption={`版本 ${batch.openapi_version}`} />
                <Metric label="新增" value={String(batch.added_count)} caption="新候选接口" tone="green" />
                <Metric label="变更" value={String(batch.changed_count)} caption="需复核结构变化" tone="amber" />
                <Metric label="阻断/排除" value={`${batch.blocked_count}/${batch.excluded_count}`} caption="不可直接发布" tone={batch.blocked_count ? "red" : "purple"} />
              </div>
              {(message || error) && <p className={`form-message ${message ? "success" : ""}`} style={{ marginTop: 14 }}>{message || error}</p>}
              <section className="panel" style={{ marginTop: 18 }}>
                <div className="toolbar">
                  <h2>导入批次</h2>
                  <span className={batch.status === "applied" ? "status good" : "status warn"}>{batch.status === "applied" ? "已确认" : "待确认"}</span>
                </div>
                <div className="code-block">{JSON.stringify(batch.summary, null, 2)}</div>
                {unresolvedBlockers.length > 0 && (
                  <p className="notice" style={{ marginTop: 14 }}>
                    当前还有 {unresolvedBlockers.length} 个阻断风险处于 pending。平台不会允许直接确认批次；请先在下方“路由映射预览”中把这些接口标记为“阻断”或“排除”。
                  </p>
                )}
                <button className="button" style={{ marginTop: 14 }} disabled={batch.status === "applied" || pendingDecisionCount > 0} title={pendingDecisionCount ? `仍有 ${pendingDecisionCount} 个接口待决策` : undefined} onClick={() => void confirmBatch()}>
                  <CheckCircle2 size={16} />
                  确认导入批次
                </button>
              </section>
              <section className="panel" style={{ marginTop: 18 }}>
                <div className="toolbar">
                  <h2>路由映射预览</h2>
                  <span className="muted">Gateway 路径、上游路径、风险与默认决策</span>
                </div>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>状态</th>
                        <th>方法</th>
                        <th>Gateway 路径</th>
                        <th>接口作用</th>
                        <th>上游路径</th>
                        <th>风险</th>
                        <th>决策</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diffs.map((diff) => (
                        <tr key={diff.id}>
                          <td>{diffLabel(diff.diff_type)}</td>
                          <td>
                            <span className={diff.method === "DELETE" ? "status warn" : "status info"}>{diff.method}</span>
                          </td>
                          <td className="endpoint-path">{diff.gateway_path}</td>
                          <td>
                            <strong>{diff.summary || diff.gateway_path}</strong>
                            <br />
                            <span className="muted">{diff.description || "上游规范未提供 description，后续可在单接口精修页补充。"}</span>
                          </td>
                          <td className="endpoint-path">{diff.upstream_path}</td>
                          <td>
                            {riskLabel(diff.risk_level)}
                            <br />
                            <span className="muted">{diff.risk_flags.join(", ") || "无显著风险"}</span>
                          </td>
                          <td>{diff.decision}</td>
                          <td>
                            <div className="toolbar-actions" style={{ gap: 8, flexWrap: "wrap" }}>
                              {diff.risk_level !== "blocker" && (
                                <button className="button secondary compact" disabled={batch.status === "applied"} onClick={() => void decide(diff, "accept")}>
                                  接受
                                </button>
                              )}
                              <button className="button secondary compact" disabled={batch.status === "applied"} onClick={() => void decide(diff, diff.risk_level === "blocker" ? "block" : "exclude")}>
                                {diff.risk_level === "blocker" && <ShieldX size={14} />}
                                {diff.risk_level === "blocker" ? "阻断" : "排除"}
                              </button>
                              {diff.risk_level === "blocker" && (
                                <button className="button secondary compact" disabled={batch.status === "applied"} onClick={() => void decide(diff, "exclude")}>
                                  排除
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          ) : (
            <div className="state-box">{loadingLatest ? "正在读取最近导入批次..." : "请先选择导入方式并生成预览。"}</div>
          )}
        </section>
      </div>
    </PortalShell>
  );
}
