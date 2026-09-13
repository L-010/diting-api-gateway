"use client";

import { useEffect, useRef, useState } from "react";
import { Copy, FileArchive, Route, ShieldCheck, Timer, X } from "lucide-react";
import { RemoteFileTable } from "@/components/remote-file-table";
import { api, errorMessage } from "@/lib/api";
import { RemoteFile } from "@/lib/remote-files";
import { copyToClipboard } from "@/lib/clipboard";
import { failureStageLabel, formatBytes, GatewayCall, statusTone } from "@/lib/calls";

export function CallDetailDrawer({ call, admin = false, onClose }: { call: GatewayCall | null; admin?: boolean; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement>(null);
  const [files, setFiles] = useState<RemoteFile[]>([]);
  const [filesError, setFilesError] = useState("");
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesReload, setFilesReload] = useState(0);
  useEffect(() => {
    if (!call) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.requestAnimationFrame(() => drawerRef.current?.querySelector<HTMLElement>("button, a")?.focus());
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && drawerRef.current) {
        const focusable = [...drawerRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')];
        if (!focusable.length) return;
        const first = focusable[0]; const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => { window.removeEventListener("keydown", closeOnEscape); previous?.focus(); };
  }, [call, onClose]);

  useEffect(() => {
    setFiles([]); setFilesError(""); setFilesLoading(Boolean(call));
    if (!call) return;
    let active = true;
    const url = admin ? `/api/admin/calls/${call.request_id}/files` : `/api/me/calls/${call.request_id}/files`;
    api<RemoteFile[]>(url)
      .then((items) => { if (active) setFiles(items); })
      .catch((cause) => { if (active) setFilesError(errorMessage(cause)); })
      .finally(() => { if (active) setFilesLoading(false); });
    return () => { active = false; };
  }, [admin, call, filesReload]);

  if (!call) return null;
  const gatewayPath = `/gateway/${call.tool_slug}${call.path}`;
  return (
    <div className="call-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside ref={drawerRef} className="call-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="call-detail-title">
        <header className="call-detail-header">
          <div>
            <span className={statusTone(call.status_code)}>{call.status_code}</span>
            <h2 id="call-detail-title">{call.endpoint_summary || call.operation_id || "Gateway 调用详情"}</h2>
            <p>{new Date(call.created_at).toLocaleString()} · {failureStageLabel(call.failure_stage)}</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭调用详情"><X size={18} /></button>
        </header>

        <div className="call-detail-body">
          <section className="call-detail-section">
            <h3><Route size={16} /> 请求定位</h3>
            <dl className="call-detail-list">
              <div><dt>request_id</dt><dd><code>{call.request_id}</code></dd></div>
              <div><dt>工具</dt><dd>{call.tool_slug || "未知工具"}</dd></div>
              <div><dt>请求方法</dt><dd><span className="status info">{call.method}</span></dd></div>
              <div><dt>Gateway 路径</dt><dd><code>{gatewayPath}</code></dd></div>
              <div><dt>operation_id</dt><dd>{call.operation_id || "未绑定"}</dd></div>
              {admin && <div><dt>上游路径</dt><dd><code>{call.upstream_path || "未进入上游"}</code></dd></div>}
            </dl>
          </section>

          <section className="call-detail-section call-files-section">
            <h3><FileArchive size={16} /> 关联文件</h3>
            {filesLoading ? <div className="state-box">正在加载关联文件...</div> : filesError ? <div className="state-box error"><p>{filesError}</p><button className="button secondary compact" type="button" onClick={() => setFilesReload((value) => value + 1)}>重试</button></div> : files.length ? <RemoteFileTable files={files} admin={admin} /> : <div className="state-box">本次调用没有已登记的{admin ? "可见" : "用户可见"}文件。</div>}
          </section>

          {admin && (
            <section className="call-detail-section">
              <h3><ShieldCheck size={16} /> 请求归属</h3>
              <dl className="call-detail-list">
                <div><dt>用户</dt><dd>{call.username || "无法归属用户"}</dd></div>
                <div><dt>Key 前缀</dt><dd>{call.api_key_prefix ? <code>{call.api_key_prefix}...</code> : "未识别 Key"}</dd></div>
                <div><dt>客户端指纹</dt><dd><code>{call.client_ip_fingerprint || "-"}</code></dd></div>
                <div><dt>User-Agent 指纹</dt><dd><code>{call.user_agent_fingerprint || "-"}</code></dd></div>
              </dl>
            </section>
          )}

          <section className="call-detail-section">
            <h3><Timer size={16} /> 执行结果</h3>
            <dl className="call-detail-list">
              <div><dt>平台状态</dt><dd><span className={statusTone(call.status_code)}>{call.status_code}</span></dd></div>
              <div><dt>上游状态</dt><dd>{call.upstream_status_code ?? "未进入或未收到响应"}</dd></div>
              <div><dt>错误码</dt><dd>{call.error_code || "调用成功"}</dd></div>
              <div><dt>失败阶段</dt><dd>{failureStageLabel(call.failure_stage)}</dd></div>
              <div><dt>总耗时</dt><dd>{call.duration_ms} ms</dd></div>
              <div><dt>流量</dt><dd>请求 {formatBytes(call.request_bytes)} / 响应 {formatBytes(call.response_bytes)}</dd></div>
            </dl>
          </section>

          <section className="call-detail-section">
            <h3>脱敏查询参数</h3>
            {call.query.length ? (
              <dl className="call-query-list">
                {call.query.map((item, index) => <div key={`${item.key}-${index}`}><dt>{item.key || "未命名"}</dt><dd>{item.value || "空值"}</dd></div>)}
              </dl>
            ) : <div className="state-box">本次请求没有查询参数。</div>}
            <p className="call-security-note">平台不保存请求正文、响应正文、完整 API Key、Authorization、Cookie 或上游凭证。</p>
          </section>
        </div>

        <footer className="call-detail-actions">
          <button className="button secondary" type="button" onClick={() => void copyToClipboard(call.request_id, "request_id 已复制")}><Copy size={15} />复制 request_id</button>
          <button className="button" type="button" onClick={onClose}>关闭</button>
        </footer>
      </aside>
    </div>
  );
}
