"use client";

import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  DatabaseZap,
  FileCode2,
  FileJson,
  KeyRound,
  Network,
  PlugZap,
  Rocket,
  ShieldCheck,
  ShieldX,
  UploadCloud,
} from "lucide-react";
import { Metric, PortalShell } from "@/components/portal-shell";
import { api, errorMessage } from "@/lib/api";
import { AccessMode, EndpointView, ImportBatchView, OpenApiDiffView, ToolView, accessModeLabel, diffLabel, riskLabel, statusLabel } from "@/lib/admin-tools";
import { notifyToast } from "@/lib/toast";

type OnboardingInfo = {
  allowed_upstream_hosts: string[];
  gateway_pattern: string;
  supported_spec_versions: string[];
  supported_methods: string[];
  max_openapi_document_bytes: number;
  max_upload_bytes: number;
  max_download_bytes: number;
  access_modes: AccessMode[];
  standard_steps: string[];
};

type ImportResult = { batch: ImportBatchView; diffs: OpenApiDiffView[] };
type HealthResult = { status: string; reachable?: boolean; status_code?: number | null; path?: string; error?: string };
type ValidationResult = { ready: boolean; health: HealthResult; checks: Array<{ key: string; label: string; passed: boolean; warning?: boolean; detail: string }>; policy_problems: Array<{ diff_id: string; reason: string }> };
type BulkPublishResult = {
  tool_id: string;
  published_count: number;
  skipped_count: number;
  published: Array<{ endpoint_id: string; method: string; gateway_path: string }>;
  skipped: Array<{ endpoint_id: string; method: string; gateway_path: string; reason: string }>;
};
type BulkDecisionResult = { updated: OpenApiDiffView[]; failed: Array<{ diff_id: string; reason: string }> };
type ManualPolicyDraft = {
  action: "require_owner" | "register_owner" | "filter_owned_list";
  resource_kind: string;
  source: "path" | "query" | "request_json" | "response_json" | "response_header";
  selector: string;
  items_selector: string;
};

type WizardForm = {
  slug: string;
  name: string;
  description: string;
  base_url: string;
  health_path: string;
  auth_type: "none" | "bearer" | "header_api_key";
  token_label: string;
  upstream_token: string;
  status: "draft" | "active" | "disabled";
  discovery_type: "fixed_url" | "openapi_url" | "manual_upload" | "text";
  source_url: string;
  document_text: string;
  default_gateway_prefix: string;
  rate_limit_per_minute: number;
  network_zone: string;
  verify_tls: boolean;
  retry_count: number;
  connect_timeout_seconds: number;
  request_timeout_seconds: number;
  network_isolation_mode: "firewall_allowlist" | "private_network" | "machine_credential_only";
  network_isolation_note: string;
  network_isolation_confirmed: boolean;
  confirm_reason: string;
  publish_reason: string;
};

const steps = [
  ["基本信息", "名称、slug、用途边界", PlugZap],
  ["部署环境", "单一上游服务器", Network],
  ["服务端认证", "Bearer / Header API Key", KeyRound],
  ["API 来源", "URL、上传或文本导入", FileCode2],
  ["导入预览", "候选接口与风险摘要", UploadCloud],
  ["公共策略", "限流、超时、审计", ShieldCheck],
  ["校验测试", "健康检查与连通性", DatabaseZap],
  ["提交发布", "确认路由并进入工作台", CheckCircle2],
] as const;

const initialForm: WizardForm = {
  slug: "",
  name: "",
  description: "",
  base_url: "http://127.0.0.1:18000",
  health_path: "/health",
  auth_type: "bearer",
  token_label: "",
  upstream_token: "",
  status: "draft",
  discovery_type: "fixed_url",
  source_url: "http://127.0.0.1:18000/openapi.json",
  document_text: "",
  default_gateway_prefix: "",
  rate_limit_per_minute: 60,
  network_zone: "local",
  verify_tls: true,
  retry_count: 0,
  connect_timeout_seconds: 10,
  request_timeout_seconds: 60,
  network_isolation_mode: "firewall_allowlist",
  network_isolation_note: "工具服务器防火墙仅允许网关服务器固定 IP 访问 API 端口",
  network_isolation_confirmed: false,
  confirm_reason: "首次接入工具，确认导入预览",
  publish_reason: "首次接入工具，发布安全候选接口",
};

function statusClass(value: string) {
  if (value === "published" || value === "candidate" || value === "active") return "status good";
  if (value === "blocked" || value === "excluded" || value === "disabled") return "status warn";
  return "status info";
}

function sourceLabel(value: WizardForm["discovery_type"]) {
  const labels: Record<WizardForm["discovery_type"], string> = {
    fixed_url: "从上游 /openapi.json 拉取",
    openapi_url: "指定 OpenAPI/Swagger URL",
    manual_upload: "上传 JSON/YAML 文件",
    text: "粘贴 JSON/YAML 文本",
  };
  return labels[value];
}

function decisionLabel(value: string) {
  const labels: Record<string, string> = {
    accept: "放行",
    block: "阻断",
    exclude: "排除",
    pending: "待判断",
    defer: "延后",
  };
  return labels[value] ?? value;
}

export default function NewToolPage() {
  const router = useRouter();
  const [onboarding, setOnboarding] = useState<OnboardingInfo | null>(null);
  const [form, setForm] = useState<WizardForm>(initialForm);
  const [tool, setTool] = useState<ToolView | null>(null);
  const [batch, setBatch] = useState<ImportBatchView | null>(null);
  const [diffs, setDiffs] = useState<OpenApiDiffView[]>([]);
  const [endpoints, setEndpoints] = useState<EndpointView[]>([]);
  const [healthResult, setHealthResult] = useState<HealthResult | null>(null);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [publishResult, setPublishResult] = useState<BulkPublishResult | null>(null);
  const [openapiFile, setOpenapiFile] = useState<File | null>(null);
  const [selectedDiffIds, setSelectedDiffIds] = useState<string[]>([]);
  const [policyModes, setPolicyModes] = useState<Record<string, AccessMode>>({});
  const [manualPolicyDrafts, setManualPolicyDrafts] = useState<Record<string, ManualPolicyDraft>>({});
  const [sharedConfirmedIds, setSharedConfirmedIds] = useState<string[]>([]);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [completedSteps, setCompletedSteps] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const unresolvedBlockers = useMemo(
    () => diffs.filter((item) => item.risk_level === "blocker" && item.decision === "pending"),
    [diffs],
  );
  const pendingDecisionCount = useMemo(() => diffs.filter((item) => item.decision === "pending").length, [diffs]);
  const selectedDiffs = useMemo(() => diffs.filter((item) => selectedDiffIds.includes(item.id)), [diffs, selectedDiffIds]);
  const allDiffsSelected = diffs.length > 0 && selectedDiffIds.length === diffs.length;
  const decisionCounts = useMemo(
    () => ({
      accept: diffs.filter((item) => item.decision === "accept").length,
      block: diffs.filter((item) => item.decision === "block").length,
      exclude: diffs.filter((item) => item.decision === "exclude").length,
      pending: diffs.filter((item) => item.decision === "pending").length,
    }),
    [diffs],
  );
  const endpointCounts = useMemo(
    () => ({
      total: endpoints.length,
      published: endpoints.filter((item) => item.status === "published").length,
      blocked: endpoints.filter((item) => item.status === "blocked").length,
      excluded: endpoints.filter((item) => item.status === "excluded").length,
      safeCandidates: endpoints.filter((item) => ["candidate", "draft"].includes(item.status) && !["high", "blocker"].includes(item.risk_level)).length,
    }),
    [endpoints],
  );
  const maxUnlockedStep = Math.max(currentStepIndex, ...Object.keys(completedSteps).map(Number).map((item) => item + 1), 0);

  useEffect(() => {
    api<OnboardingInfo>("/api/admin/platform/onboarding")
      .then(setOnboarding)
      .catch((cause) => showError(errorMessage(cause), false));
  }, []);

  function updateField<K extends keyof WizardForm>(key: K, value: WizardForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateNumberField(key: keyof WizardForm, value: string) {
    setForm((current) => ({ ...current, [key]: Number(value || 0) }));
  }

  function showSuccess(nextMessage: string, toast = true) {
    setError("");
    setMessage(nextMessage);
    if (toast) notifyToast({ type: "success", message: nextMessage });
  }

  function showError(nextError: string, toast = true) {
    setMessage("");
    setError(nextError);
    if (toast) notifyToast({ type: "error", message: nextError });
  }

  function completeStep(nextMessage: string) {
    setCompletedSteps((current) => ({ ...current, [currentStepIndex]: true }));
    setCurrentStepIndex((current) => Math.min(current + 1, steps.length - 1));
    showSuccess(nextMessage);
  }

  function validateStep(index: number) {
    if (index === 0) {
      if (!/^[a-z0-9-]{2,64}$/.test(form.slug.trim())) return "工具 slug 只能使用 2-64 位小写字母、数字和短横线。";
      if (form.name.trim().length < 2) return "显示名称至少需要 2 个字符。";
      return "";
    }
    if (index === 1) {
      if (!/^https?:\/\/[^/?#]+$/i.test(form.base_url.trim())) return "上游根地址必须是 http/https 根地址，不能带路径、查询、片段或凭据。";
      if (!form.health_path.trim().startsWith("/")) return "健康检查路径必须以 / 开头。";
      return "";
    }
    if (index === 2) {
      if (form.auth_type !== "none" && !form.upstream_token.trim() && !tool?.token_configured) return "请选择无认证，或填写上游 Token / API Key。";
      if (form.auth_type === "header_api_key" && !form.token_label.trim()) return "Header API Key 认证需要填写 Header 名称。";
      return "";
    }
    if (index === 3) {
      if (form.discovery_type === "openapi_url" && !form.source_url.trim()) return "请填写 OpenAPI/Swagger 文档 URL。";
      if (form.discovery_type === "manual_upload" && !openapiFile) return "请选择 JSON/YAML 规范文件。";
      if (form.discovery_type === "text" && !form.document_text.trim()) return "请粘贴 OpenAPI/Swagger 文档文本。";
      return "";
    }
    if (index === 5) {
      if (form.rate_limit_per_minute < 1 || form.rate_limit_per_minute > 10000) return "每分钟限流必须在 1 到 10000 之间。";
      if (form.connect_timeout_seconds < 1 || form.connect_timeout_seconds > 60) return "连接超时必须在 1 到 60 秒之间。";
      if (form.request_timeout_seconds < 1 || form.request_timeout_seconds > 600) return "请求超时必须在 1 到 600 秒之间。";
      return "";
    }
    return "";
  }

  function toolPayload(overrides: Partial<WizardForm> = {}) {
    const next = { ...form, ...overrides };
    return {
      slug: next.slug.trim().toLowerCase(),
      name: next.name.trim(),
      description: next.description.trim(),
      base_url: next.base_url.trim(),
      upstream_token: next.upstream_token,
      rate_limit_per_minute: next.rate_limit_per_minute,
      status: next.status,
      default_gateway_prefix: next.default_gateway_prefix.trim(),
      auth_type: next.auth_type,
      token_label: next.token_label.trim(),
      health_path: next.health_path.trim() || "/health",
      discovery_type: next.discovery_type,
      network_zone: next.network_zone.trim() || "local",
      verify_tls: next.verify_tls,
      retry_count: next.retry_count,
      connect_timeout_seconds: next.connect_timeout_seconds,
      request_timeout_seconds: next.request_timeout_seconds,
      network_isolation_mode: next.network_isolation_mode,
      network_isolation_note: next.network_isolation_note.trim(),
      network_isolation_confirmed: next.network_isolation_confirmed,
    };
  }

  async function saveToolDraft(overrides: Partial<WizardForm> = {}) {
    const nextForm = { ...form, ...overrides };
    setForm(nextForm);
    const saved = await api<ToolView>("/api/admin/tools", {
      method: "POST",
      body: JSON.stringify(toolPayload(overrides)),
    });
    setTool(saved);
    return saved;
  }

  async function refreshTool(toolId: string) {
    const fresh = await api<ToolView>(`/api/admin/tools/${toolId}`);
    setTool(fresh);
    return fresh;
  }

  async function refreshEndpoints(toolId: string) {
    const rows = await api<EndpointView[]>(`/api/admin/tools/${toolId}/endpoints`);
    setEndpoints(rows);
    return rows;
  }

  async function goNext() {
    const validationError = validateStep(currentStepIndex);
    if (validationError) {
      showError(validationError);
      return;
    }
    if (currentStepIndex <= 2) {
      completeStep(`${steps[currentStepIndex][0]}已完成。`);
      return;
    }
    if (currentStepIndex === 3) {
      setBusy("保存工具草稿");
      try {
        const saved = await saveToolDraft();
        await refreshEndpoints(saved.id);
        completeStep("工具草稿已保存，已进入导入预览。");
      } catch (cause) {
        showError(errorMessage(cause));
      } finally {
        setBusy("");
      }
      return;
    }
    if (currentStepIndex === 4) {
      await continueImportPreview();
      return;
    }
    if (currentStepIndex === 5) {
      setBusy("保存公共策略");
      try {
        const saved = await saveToolDraft();
        await refreshTool(saved.id);
        completeStep("公共策略已保存，已进入校验测试。");
      } catch (cause) {
        showError(errorMessage(cause));
      } finally {
        setBusy("");
      }
      return;
    }
    if (currentStepIndex === 6) {
      await runHealthTest(true);
    }
  }

  async function continueImportPreview() {
    if (!tool) {
      showError("请先完成 API 来源步骤并保存工具草稿。");
      return;
    }
    if (!batch) {
      await runImportPreview();
      return;
    }
    if (batch.status !== "applied") {
      await confirmBatch();
      return;
    }
    completeStep("导入预览已确认，已进入公共策略。");
  }

  async function runImportPreview() {
    if (!tool) return;
    const validationError = validateStep(3);
    if (validationError) {
      showError(validationError);
      return;
    }
    setBusy("生成导入预览");
    setError("");
    setMessage("");
    try {
      let result: ImportResult;
      if (form.discovery_type === "manual_upload") {
        const upload = new FormData();
        if (!openapiFile) throw new Error("请选择 JSON/YAML 规范文件。");
        upload.set("file", openapiFile);
        result = await api<ImportResult>(`/api/admin/tools/${tool.id}/openapi/upload`, { method: "POST", body: upload });
      } else {
        result = await api<ImportResult>(`/api/admin/tools/${tool.id}/openapi/parse`, {
          method: "POST",
          body: JSON.stringify({
            source_type: form.discovery_type === "openapi_url" ? "url" : form.discovery_type === "text" ? "text" : "upstream",
            source_url: form.source_url,
            document_text: form.document_text,
          }),
        });
      }
      setBatch(result.batch);
      setDiffs(result.diffs);
      setSelectedDiffIds([]);
      await refreshEndpoints(tool.id);
      showSuccess("导入预览已生成，请确认风险决策后继续。");
    } catch (cause) {
      showError(errorMessage(cause));
    } finally {
      setBusy("");
    }
  }

  async function confirmBatch() {
    if (!batch || !tool) return;
    if (unresolvedBlockers.length) {
      showError(`仍有 ${unresolvedBlockers.length} 个阻断风险差异未决策，请先标记为阻断或排除。`);
      return;
    }
    if (pendingDecisionCount) {
      showError(`仍有 ${pendingDecisionCount} 个接口未判断，请先全部放行或阻断。`);
      return;
    }
    if (!form.confirm_reason.trim()) {
      showError("请输入确认导入批次的审计原因。");
      return;
    }
    setBusy("确认导入批次");
    try {
      const updated = await api<ImportBatchView>(`/api/admin/import-batches/${batch.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, reason: form.confirm_reason.trim() }),
      });
      setBatch(updated);
      await refreshEndpoints(tool.id);
      completeStep("导入批次已确认，已进入公共策略。");
    } catch (cause) {
      showError(errorMessage(cause));
    } finally {
      setBusy("");
    }
  }

  function accessPolicyPayload(diff: OpenApiDiffView, forcedMode?: AccessMode) {
    const accessMode = forcedMode ?? policyModes[diff.id] ?? diff.access_policy_suggestion.access_mode;
    let rules = accessMode === diff.access_policy_suggestion.access_mode ? diff.access_policy_suggestion.rules : { version: 1, pre_checks: [], post_actions: [], list_filter: null };
    const draft = manualPolicyDrafts[diff.id];
    if (accessMode === "owner" && (draft || (!rules.pre_checks?.length && !rules.post_actions?.length && !rules.list_filter))) {
      const resolved = draft ?? defaultManualPolicy(diff);
      if (resolved.action === "filter_owned_list") {
        rules = {
          version: 1,
          pre_checks: [],
          post_actions: [],
          list_filter: {
            items_selector: resolved.items_selector,
            id_selector: resolved.selector,
            resource_kind: resolved.resource_kind,
          },
        };
      } else {
        const rule = { action: resolved.action, resource_kind: resolved.resource_kind, source: resolved.source, selector: resolved.selector };
        rules = {
          version: 1,
          pre_checks: resolved.action === "require_owner" ? [rule] : [],
          post_actions: resolved.action === "register_owner" ? [rule] : [],
          list_filter: null,
        };
      }
    }
    return { access_mode: accessMode, rules, shared_confirm: sharedConfirmedIds.includes(diff.id) };
  }

  function defaultManualPolicy(diff: OpenApiDiffView): ManualPolicyDraft {
    const pathParam = [...diff.gateway_path.matchAll(/\{([^/{}]+_id)\}/g)][0]?.[1] ?? "";
    const pathSegments = diff.gateway_path.split("/").filter((item) => item && !item.startsWith("{"));
    const collection = pathSegments[pathSegments.length - 1] ?? "resource";
    const resourceKind = (pathParam ? pathParam.replace(/_id$/, "") : collection.replace(/ies$/, "y").replace(/s$/, "")) || "resource";
    if (diff.method === "GET" && !pathParam) {
      return { action: "filter_owned_list", resource_kind: resourceKind, source: "response_json", selector: `$.${resourceKind}_id`, items_selector: "$" };
    }
    if (diff.method === "POST" && !pathParam) {
      return { action: "register_owner", resource_kind: resourceKind, source: "response_json", selector: `$.${resourceKind}_id`, items_selector: "$" };
    }
    return { action: "require_owner", resource_kind: resourceKind, source: "path", selector: pathParam, items_selector: "$" };
  }

  function manualPolicy(diff: OpenApiDiffView) {
    return manualPolicyDrafts[diff.id] ?? defaultManualPolicy(diff);
  }

  function updateManualPolicy(diff: OpenApiDiffView, patch: Partial<ManualPolicyDraft>) {
    setManualPolicyDrafts((current) => ({ ...current, [diff.id]: { ...manualPolicy(diff), ...patch } }));
  }

  async function decide(diff: OpenApiDiffView, decision: "accept" | "exclude" | "block") {
    const policy = accessPolicyPayload(diff);
    if (decision === "accept" && policy.access_mode === "shared" && diff.access_policy_suggestion.missing_fields.includes("shared_confirm") && !policy.shared_confirm) {
      showError("全局列表设为共享数据前，必须勾选该接口的共享确认。所有已审批用户都将看到上游返回的全部数据。");
      return;
    }
    if (decision === "accept" && policy.access_mode === "owner" && !policy.rules.pre_checks?.length && !policy.rules.post_actions?.length && !policy.rules.list_filter) {
      showError("创建者资源策略必须填写资源类型、ID 来源和选择器。列表接口还必须填写列表 JSONPath。");
      return;
    }
    setBusy("更新风险决策");
    try {
      const updated = await api<OpenApiDiffView>(`/api/admin/diffs/${diff.id}/decision`, {
        method: "PATCH",
        body: JSON.stringify({ decision, reason: form.confirm_reason.trim() || "首次接入导入预览决策", access_policy: decision === "accept" ? policy : undefined, use_suggestion: true }),
      });
      setDiffs((rows) => rows.map((item) => (item.id === updated.id ? updated : item)));
      if (tool) await refreshEndpoints(tool.id);
      showSuccess("差异决策已更新。");
    } catch (cause) {
      showError(errorMessage(cause));
    } finally {
      setBusy("");
    }
  }

  async function runHealthTest(advance = false) {
    const saved = tool ?? (await saveToolDraft());
    setBusy("执行发布前校验");
    try {
      const result = await api<ValidationResult>(`/api/admin/tools/${saved.id}/validation`, { method: "POST" });
      setValidationResult(result);
      setHealthResult(result.health);
      await refreshTool(saved.id);
      if (advance) {
        if (!result.ready) {
          showError(`发布前校验未通过：${result.checks.filter((item) => !item.passed).map((item) => item.label).join("、")}`);
          return;
        }
        completeStep("发布前校验全部通过，已进入提交发布。");
      } else {
        result.ready ? showSuccess("发布前校验全部通过。") : showError("发布前校验存在未通过项目。请根据列表修正后重试。");
      }
    } catch (cause) {
      showError(errorMessage(cause));
    } finally {
      setBusy("");
    }
  }

  async function bulkPublish() {
    if (!form.publish_reason.trim()) {
      showError("请输入批量发布的审计原因。");
      return;
    }
    setBusy("发布安全路由");
    try {
      const saved = await saveToolDraft({ status: "active" });
      const result = await api<BulkPublishResult>(`/api/admin/tools/${saved.id}/endpoints/bulk-publish`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, reason: form.publish_reason.trim(), max_risk_level: "medium" }),
      });
      setPublishResult(result);
      await Promise.all([refreshTool(saved.id), refreshEndpoints(saved.id)]);
      setCompletedSteps((current) => ({ ...current, [currentStepIndex]: true }));
      showSuccess(`已原子发布全部 ${result.published_count} 个放行接口。`);
    } catch (cause) {
      showError(errorMessage(cause));
    } finally {
      setBusy("");
    }
  }

  async function finishAsDraft() {
    setBusy("保存草稿");
    try {
      const saved = await saveToolDraft();
      router.push(`/admin/tools/${saved.id}`);
    } catch (cause) {
      showError(errorMessage(cause));
      setBusy("");
    }
  }

  function selectStep(index: number) {
    if (index <= maxUnlockedStep) setCurrentStepIndex(index);
  }

  function toggleDiffSelection(diffId: string, checked: boolean) {
    setSelectedDiffIds((current) => {
      if (checked) return current.includes(diffId) ? current : [...current, diffId];
      return current.filter((item) => item !== diffId);
    });
  }

  function toggleAllDiffs(checked: boolean) {
    setSelectedDiffIds(checked ? diffs.map((item) => item.id) : []);
  }

  async function bulkDecideSelected(decision: "accept" | "block", accessMode?: AccessMode) {
    if (!selectedDiffs.length) {
      showError("请先选择要批量处理的接口。");
      return;
    }
    const label = decision === "accept" ? "放行" : "阻断";
    setBusy(`批量${label}`);
    setError("");
    setMessage("");
    try {
      const result = await api<BulkDecisionResult>("/api/admin/diffs/bulk-decision", {
        method: "POST",
        body: JSON.stringify({
          diff_ids: selectedDiffIds,
          decision,
          reason: form.confirm_reason.trim() || `首次接入导入预览批量${label}`,
          access_policy: decision === "accept" && accessMode ? { access_mode: accessMode, rules: { version: 1, pre_checks: [], post_actions: [], list_filter: null }, shared_confirm: accessMode === "shared" } : undefined,
          use_suggestions: !accessMode,
        }),
      });
      const updatedRows = result.updated;
      if (updatedRows.length) {
        setDiffs((rows) => rows.map((item) => updatedRows.find((updated) => updated.id === item.id) ?? item));
      }
      setSelectedDiffIds([]);
      if (tool) await refreshEndpoints(tool.id);
      const failedCount = result.failed.length;
      if (failedCount) {
        showError(`已${label} ${updatedRows.length} 个接口，${failedCount} 个策略不完整：${result.failed[0]?.reason || "请逐项确认"}`);
      } else {
        showSuccess(`已批量${label} ${updatedRows.length} 个接口。`);
      }
    } finally {
      setBusy("");
    }
  }

  function renderStepContent() {
    if (currentStepIndex === 0) {
      return (
        <div className="inline-form">
          <label className="form-field">
            工具 slug
            <input value={form.slug} onChange={(event) => updateField("slug", event.target.value.toLowerCase())} required pattern="[a-z0-9-]{2,64}" placeholder="例如 tomodd 或 openai-api" />
            <span className="muted">创建后不可修改；Gateway 前缀为 /gateway/{"{slug}"}。</span>
          </label>
          <label className="form-field">
            显示名称
            <input value={form.name} onChange={(event) => updateField("name", event.target.value)} required minLength={2} placeholder="例如 tomoDD-SP 走时成像 API" />
          </label>
          <label className="form-field full">
            工具说明
            <textarea value={form.description} onChange={(event) => updateField("description", event.target.value)} placeholder="说明工具用途、适用用户、不可发布的高风险能力。" />
          </label>
        </div>
      );
    }
    if (currentStepIndex === 1) {
      return (
        <div className="inline-form">
          <label className="form-field full">
            部署服务器根地址
            <input value={form.base_url} onChange={(event) => updateField("base_url", event.target.value)} required placeholder="例如 http://127.0.0.1:18000" />
            <span className="muted">每个工具只配置一个部署服务器；必须命中后端 ALLOWED_UPSTREAM_HOSTS，且不能携带路径、查询或凭据。</span>
          </label>
          <label className="form-field">
            健康检查路径
            <input value={form.health_path} onChange={(event) => updateField("health_path", event.target.value)} placeholder="/health" />
          </label>
          <label className="form-field">
            网络区域
            <input value={form.network_zone} onChange={(event) => updateField("network_zone", event.target.value)} placeholder="local" />
          </label>
          <label className="form-field">
            网络隔离方式
            <select value={form.network_isolation_mode} onChange={(event) => updateField("network_isolation_mode", event.target.value as WizardForm["network_isolation_mode"])}>
              <option value="firewall_allowlist">防火墙仅放行网关 IP</option>
              <option value="private_network">私网 / VPN</option>
              <option value="machine_credential_only">机器凭证保护</option>
            </select>
          </label>
          <label className="form-field full">
            隔离说明
            <input value={form.network_isolation_note} onChange={(event) => updateField("network_isolation_note", event.target.value)} placeholder="记录安全组、系统防火墙或私网边界" />
          </label>
          <label className="form-field full">
            <span>
              <input checked={form.network_isolation_confirmed} onChange={(event) => updateField("network_isolation_confirmed", event.target.checked)} type="checkbox" style={{ marginRight: 8 }} />
              已确认用户无法绕过网关直接访问该工具服务器
            </span>
          </label>
          <div className="signal-card neutral full">
            <strong>允许上游主机</strong>
            <p className="muted">{(onboarding?.allowed_upstream_hosts || []).join(", ") || "正在读取白名单..."}</p>
          </div>
        </div>
      );
    }
    if (currentStepIndex === 2) {
      return (
        <div className="inline-form">
          <label className="form-field">
            服务端认证类型
            <select value={form.auth_type} onChange={(event) => updateField("auth_type", event.target.value as WizardForm["auth_type"])}>
              <option value="bearer">Bearer Token</option>
              <option value="header_api_key">Header API Key</option>
              <option value="none">无服务端认证</option>
            </select>
          </label>
          <label className="form-field">
            Header 名称
            <input value={form.token_label} onChange={(event) => updateField("token_label", event.target.value)} placeholder="例如 X-API-Key" disabled={form.auth_type !== "header_api_key"} />
          </label>
          <label className="form-field full">
            上游 Token / API Key
            <input value={form.upstream_token} onChange={(event) => updateField("upstream_token", event.target.value)} type="password" placeholder="仅后端加密保存；不会在页面、日志或审计中回显。" disabled={form.auth_type === "none"} />
          </label>
          {tool?.token_configured && <p className="notice full">当前工具已配置服务端密钥；留空不会覆盖旧密钥。</p>}
          <div className="signal-card info full">
            <strong>机器凭证边界</strong>
            <p className="muted">该凭证只证明请求来自网关。最终用户身份、接口权限和资源归属全部由当前网关判断，客户端凭证不会转发给上游。</p>
          </div>
        </div>
      );
    }
    if (currentStepIndex === 3) {
      return (
        <div className="inline-form">
          <label className="form-field">
            API 来源
            <select value={form.discovery_type} onChange={(event) => updateField("discovery_type", event.target.value as WizardForm["discovery_type"])}>
              <option value="fixed_url">上游 /openapi.json</option>
              <option value="openapi_url">指定 OpenAPI/Swagger URL</option>
              <option value="manual_upload">上传 JSON/YAML 文件</option>
              <option value="text">文本粘贴</option>
            </select>
          </label>
          <label className="form-field">
            Gateway 路径前缀
            <input value={form.default_gateway_prefix} onChange={(event) => updateField("default_gateway_prefix", event.target.value)} placeholder="例如 /api/v1，留空则使用规范原路径" />
          </label>
          {form.discovery_type === "openapi_url" && (
            <label className="form-field full">
              文档 URL
              <input value={form.source_url} onChange={(event) => updateField("source_url", event.target.value)} placeholder="http://127.0.0.1:18000/openapi.json" />
            </label>
          )}
          {form.discovery_type === "manual_upload" && (
            <label className="upload-zone full">
              <UploadCloud size={22} />
              <strong>上传规范文件</strong>
              <input type="file" accept=".json,.yaml,.yml,application/json,text/yaml" onChange={(event: ChangeEvent<HTMLInputElement>) => setOpenapiFile(event.target.files?.[0] ?? null)} />
              <span className="muted">{openapiFile ? openapiFile.name : "最大 2 MiB；文件内容不会被原样回显。"}</span>
            </label>
          )}
          {form.discovery_type === "text" && (
            <label className="form-field full">
              文档文本
              <textarea value={form.document_text} onChange={(event) => updateField("document_text", event.target.value)} rows={14} placeholder="粘贴 OpenAPI/Swagger JSON 或 YAML" />
            </label>
          )}
          <div className="signal-card info full">
            <strong>来源摘要</strong>
            <p className="muted">
              {sourceLabel(form.discovery_type)}
              <br />
              支持：{onboarding?.supported_spec_versions?.join(" / ") || "OpenAPI 3.0/3.1 / Swagger 2.0"}
            </p>
          </div>
        </div>
      );
    }
    if (currentStepIndex === 4) {
      return (
        <div>
          {batch ? (
            <>
              <div className="metrics">
                <Metric label="接口总数" value={String(batch.total_operations)} caption={`版本 ${batch.openapi_version}`} />
                <Metric label="已放行" value={String(decisionCounts.accept)} caption="允许进入发布" tone="green" />
                <Metric label="已阻断" value={String(decisionCounts.block)} caption="发布时不上线" tone="red" />
                <Metric label="待判断" value={String(decisionCounts.pending)} caption="需放行或阻断" tone={decisionCounts.pending ? "amber" : "green"} />
              </div>
              <div className="inline-form" style={{ marginTop: 18 }}>
                <label className="form-field full">
                  确认导入批次审计原因
                  <input value={form.confirm_reason} onChange={(event) => updateField("confirm_reason", event.target.value)} />
                </label>
              </div>
              <div className="toolbar">
                <div>
                  <h3 style={{ margin: 0 }}>接口判断</h3>
                  <span className="muted">已选择 {selectedDiffIds.length} / {diffs.length} 个接口</span>
                </div>
                <div className="toolbar-actions" style={{ flexWrap: "wrap" }}>
                  <button className="button secondary compact" type="button" onClick={() => toggleAllDiffs(!allDiffsSelected)} disabled={!diffs.length || Boolean(busy)}>
                    {allDiffsSelected ? "取消全选" : "全选"}
                  </button>
                  <button className="button secondary compact" type="button" onClick={() => void bulkDecideSelected("accept")} disabled={!selectedDiffIds.length || Boolean(busy)}>
                    <Check size={14} />
                    批量采用建议并放行
                  </button>
                  <button className="button secondary compact" type="button" onClick={() => void bulkDecideSelected("accept", "shared")} disabled={!selectedDiffIds.length || Boolean(busy)}>
                    批量设为共享
                  </button>
                  <button className="button secondary compact" type="button" onClick={() => void bulkDecideSelected("accept", "admin_only")} disabled={!selectedDiffIds.length || Boolean(busy)}>
                    批量设为仅管理员
                  </button>
                  <button className="button secondary compact" type="button" onClick={() => void bulkDecideSelected("block")} disabled={!selectedDiffIds.length || Boolean(busy)}>
                    <ShieldX size={14} />
                    批量阻断
                  </button>
                  <span className={batch.status === "applied" ? "status good" : "status warn"}>{batch.status === "applied" ? "已确认" : "待确认"}</span>
                </div>
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>
                        <input checked={allDiffsSelected} type="checkbox" onChange={(event) => toggleAllDiffs(event.target.checked)} aria-label="选择全部接口" />
                      </th>
                      <th>类型</th>
                      <th>方法</th>
                      <th>接口作用</th>
                      <th>Gateway 路径</th>
                      <th>风险</th>
                      <th>访问策略</th>
                      <th>决策</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diffs.map((diff) => (
                      <tr key={diff.id}>
                        <td>
                          <input checked={selectedDiffIds.includes(diff.id)} type="checkbox" onChange={(event) => toggleDiffSelection(diff.id, event.target.checked)} aria-label={`选择 ${diff.method} ${diff.gateway_path}`} />
                        </td>
                        <td>{diffLabel(diff.diff_type)}</td>
                        <td>
                          <span className={diff.method === "DELETE" ? "status warn" : "status info"}>{diff.method}</span>
                        </td>
                        <td>
                          <strong>{diff.summary || diff.gateway_path}</strong>
                          {diff.description && (
                            <>
                              <br />
                              <span className="muted">{diff.description}</span>
                            </>
                          )}
                        </td>
                        <td>
                          <span className="endpoint-path">{diff.gateway_path}</span>
                        </td>
                        <td>
                          {riskLabel(diff.risk_level)}
                          <br />
                          <span className="muted">{diff.risk_flags.join(", ") || "无显著风险"}</span>
                        </td>
                        <td>
                          <select
                            value={policyModes[diff.id] ?? diff.access_policy_suggestion.access_mode}
                            onChange={(event) => setPolicyModes((current) => ({ ...current, [diff.id]: event.target.value as AccessMode }))}
                            aria-label={`${diff.method} ${diff.gateway_path} 访问策略`}
                          >
                            <option value="authenticated">平台用户</option>
                            <option value="owner">创建者资源</option>
                            <option value="shared">共享数据</option>
                            <option value="admin_only">仅管理员</option>
                          </select>
                          <br />
                          <span className="muted">{diff.access_policy_suggestion.reason}</span>
                          {(policyModes[diff.id] ?? diff.access_policy_suggestion.access_mode) === "shared" && diff.access_policy_suggestion.missing_fields.includes("shared_confirm") && (
                            <label style={{ display: "block", marginTop: 6 }}>
                              <input
                                checked={sharedConfirmedIds.includes(diff.id)}
                                onChange={(event) => setSharedConfirmedIds((current) => event.target.checked ? [...new Set([...current, diff.id])] : current.filter((item) => item !== diff.id))}
                                type="checkbox"
                                style={{ marginRight: 6 }}
                              />
                              确认所有用户共享全量结果
                            </label>
                          )}
                          {(policyModes[diff.id] ?? diff.access_policy_suggestion.access_mode) === "owner" &&
                            ((policyModes[diff.id] ?? diff.access_policy_suggestion.access_mode) !== diff.access_policy_suggestion.access_mode ||
                              (!diff.access_policy_suggestion.rules.pre_checks?.length && !diff.access_policy_suggestion.rules.post_actions?.length && !diff.access_policy_suggestion.rules.list_filter)) && (
                            <div style={{ display: "grid", gap: 6, marginTop: 8, minWidth: 220 }}>
                              <input
                                value={manualPolicy(diff).resource_kind}
                                onChange={(event) => updateManualPolicy(diff, { resource_kind: event.target.value.trim() })}
                                placeholder="资源类型，例如 dataset"
                                aria-label={`${diff.gateway_path} 资源类型`}
                              />
                              <select value={manualPolicy(diff).action} onChange={(event) => updateManualPolicy(diff, { action: event.target.value as ManualPolicyDraft["action"] })}>
                                <option value="require_owner">调用前校验归属</option>
                                <option value="register_owner">成功后登记归属</option>
                                <option value="filter_owned_list">过滤归属列表</option>
                              </select>
                              {manualPolicy(diff).action !== "filter_owned_list" && (
                                <select value={manualPolicy(diff).source} onChange={(event) => updateManualPolicy(diff, { source: event.target.value as ManualPolicyDraft["source"] })}>
                                  <option value="path">路径参数</option>
                                  <option value="query">查询参数</option>
                                  <option value="request_json">请求 JSONPath</option>
                                  <option value="response_json">响应 JSONPath</option>
                                  <option value="response_header">响应头</option>
                                </select>
                              )}
                              {manualPolicy(diff).action === "filter_owned_list" && (
                                <input
                                  value={manualPolicy(diff).items_selector}
                                  onChange={(event) => updateManualPolicy(diff, { items_selector: event.target.value })}
                                  placeholder="列表 JSONPath，例如 $.items"
                                  aria-label={`${diff.gateway_path} 列表 JSONPath`}
                                />
                              )}
                              <input
                                value={manualPolicy(diff).selector}
                                onChange={(event) => updateManualPolicy(diff, { selector: event.target.value })}
                                placeholder={manualPolicy(diff).source.includes("json") || manualPolicy(diff).action === "filter_owned_list" ? "ID JSONPath，例如 $.id" : "参数名，例如 dataset_id"}
                                aria-label={`${diff.gateway_path} ID 选择器`}
                              />
                            </div>
                          )}
                        </td>
                        <td>
                          {decisionLabel(diff.decision)}
                          {diff.decision === "accept" && <><br /><span className="status good">{accessModeLabel(diff.access_policy.access_mode)}</span></>}
                        </td>
                        <td>
                          <div className="toolbar-actions" style={{ gap: 8, flexWrap: "wrap" }}>
                            <button className="button secondary compact" type="button" onClick={() => void decide(diff, "accept")} disabled={Boolean(busy)}>
                              <Check size={14} />
                              放行
                            </button>
                            <button className="button secondary compact" type="button" onClick={() => void decide(diff, "block")} disabled={Boolean(busy)}>
                              <ShieldX size={14} />
                              阻断
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted">当前批次共 {diffs.length} 个接口；全部接口都会在这里完成放行或阻断判断。</p>
            </>
          ) : (
            <div className="state-box">
              <FileJson size={28} />
              <p>点击下方按钮生成导入预览。</p>
            </div>
          )}
        </div>
      );
    }
    if (currentStepIndex === 5) {
      return (
        <div className="inline-form">
          <label className="form-field">
            工具状态
            <select value={form.status} onChange={(event) => updateField("status", event.target.value as WizardForm["status"])}>
              <option value="draft">草稿</option>
              <option value="active">启用</option>
              <option value="disabled">停用</option>
            </select>
          </label>
          <label className="form-field">
            每分钟限流
            <input value={form.rate_limit_per_minute} onChange={(event) => updateNumberField("rate_limit_per_minute", event.target.value)} type="number" min={1} max={10000} />
          </label>
          <label className="form-field">
            重试次数
            <input value={form.retry_count} onChange={(event) => updateNumberField("retry_count", event.target.value)} type="number" min={0} max={5} />
          </label>
          <label className="form-field">
            连接超时（秒）
            <input value={form.connect_timeout_seconds} onChange={(event) => updateNumberField("connect_timeout_seconds", event.target.value)} type="number" min={1} max={60} />
          </label>
          <label className="form-field">
            请求超时（秒）
            <input value={form.request_timeout_seconds} onChange={(event) => updateNumberField("request_timeout_seconds", event.target.value)} type="number" min={1} max={600} />
          </label>
          <label className="form-field full">
            TLS 校验
            <span>
              <input checked={form.verify_tls} onChange={(event) => updateField("verify_tls", event.target.checked)} type="checkbox" style={{ marginRight: 8 }} />
              对 HTTPS 上游启用证书校验
            </span>
          </label>
          <div className="signal-card info full">
            <strong>接口鉴权策略</strong>
            <p className="muted">
              已确认 {diffs.filter((item) => item.decision === "accept" && item.access_policy_complete).length} 个，待判断 {decisionCounts.pending} 个。发布时会把已确认策略复制为线上快照；创建者资源由网关登记并隔离。
            </p>
          </div>
        </div>
      );
    }
    if (currentStepIndex === 6) {
      return (
        <div className="response-card">
          <div className={healthResult?.reachable ? "signal-card info" : "signal-card neutral"}>
            <strong>健康检查</strong>
            <p className="muted">
              路径：{form.health_path || "/health"}
              <br />
              状态：{healthResult ? `${healthResult.status} / ${healthResult.status_code ?? "无响应"}` : "尚未执行"}
              {healthResult?.error ? (
                <>
                  <br />
                  错误：{healthResult.error}
                </>
              ) : null}
            </p>
          </div>
          <button className="button secondary" type="button" onClick={() => void runHealthTest(false)} disabled={Boolean(busy)}>
            <DatabaseZap size={16} />
            重新校验
          </button>
          {validationResult && (
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>校验项</th><th>结果</th><th>详情</th></tr></thead>
                <tbody>
                  {validationResult.checks.map((item) => (
                    <tr key={item.key}>
                      <td>{item.label}</td>
                      <td><span className={item.passed ? item.warning ? "status warn" : "status good" : "status warn"}>{item.passed ? item.warning ? "通过（有提示）" : "通过" : "未通过"}</span></td>
                      <td className="muted">{item.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      );
    }
    return (
      <div className="response-card">
        <div className="metrics">
          <Metric label="接口总数" value={String(endpointCounts.total)} caption="已导入候选接口" />
          <Metric label="已放行" value={String(decisionCounts.accept)} caption="策略已确认后统一发布" tone="green" />
          <Metric label="已发布" value={String(endpointCounts.published)} caption="可被 X-API-Key 调用" tone="purple" />
          <Metric label="阻断/排除" value={`${endpointCounts.blocked}/${endpointCounts.excluded}`} caption="留在工作台治理" tone={endpointCounts.blocked ? "red" : "amber"} />
        </div>
        <label className="form-field">
          发布审计原因
          <input value={form.publish_reason} onChange={(event) => updateField("publish_reason", event.target.value)} />
        </label>
        {publishResult && (
          <div className="signal-card info">
            <strong>发布结果</strong>
            <p className="muted">
              已原子发布全部 {publishResult.published_count} 个放行接口；本次没有跳过接口。
            </p>
          </div>
        )}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>方法</th>
                <th>Gateway 路径</th>
                <th>状态</th>
                <th>风险</th>
              </tr>
            </thead>
            <tbody>
              {endpoints.slice(0, 12).map((endpoint) => (
                <tr key={endpoint.id}>
                  <td>
                    <span className={endpoint.method === "DELETE" ? "status warn" : "status info"}>{endpoint.method}</span>
                  </td>
                  <td className="endpoint-path">{endpoint.gateway_path}</td>
                  <td>
                    <span className={statusClass(endpoint.status)}>{statusLabel(endpoint.status)}</span>
                  </td>
                  <td>{riskLabel(endpoint.risk_level)}</td>
                </tr>
              ))}
              {!endpoints.length && (
                <tr>
                  <td colSpan={4}>暂无接口，请先完成导入预览。</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  const [stepTitle] = steps[currentStepIndex];
  const actionLabel =
    currentStepIndex === 4
      ? !batch
        ? "生成导入预览"
        : batch.status === "applied"
          ? "进入公共策略"
          : "确认预览并继续"
      : currentStepIndex === 6
        ? "执行校验并继续"
        : "完成本步";

  return (
    <PortalShell admin title="新建工具接入向导">
      <p className="page-intro">
        第一次接入新工具时，八步流程都在当前页面完成；每完成一步，右侧自动进入下一步，左侧同步标记进度。
      </p>

      <div className="page-split wizard-layout">
        <aside className="panel">
          <h2>8 步接入流程</h2>
          <div className="step-list">
            {steps.map(([title, desc, Icon], index) => {
              const done = Boolean(completedSteps[index]);
              const unlocked = index <= maxUnlockedStep;
              return (
                <button className={`${done ? "step-item done" : "step-item"} ${index === currentStepIndex ? "active" : ""}`} disabled={!unlocked} key={title} onClick={() => selectStep(index)} type="button">
                  <span className="step-index">{done ? <Check size={17} /> : index + 1}</span>
                  <div>
                    <strong>
                      <Icon size={15} style={{ verticalAlign: "middle", marginRight: 6 }} />
                      {title}
                    </strong>
                    <small>{desc}</small>
                  </div>
                </button>
              );
            })}
          </div>
          <div className="signal-card info" style={{ marginTop: 18 }}>
            <strong>当前接入边界</strong>
            <p className="muted">
              Gateway：<code>{tool ? `/gateway/${tool.slug}{gateway_path}` : onboarding?.gateway_pattern || "/gateway/{tool_slug}{gateway_path}"}</code>
              <br />
              方法：{onboarding?.supported_methods?.join(", ") || "GET, POST, PUT, PATCH, DELETE, HEAD"}
              <br />
              来源：{sourceLabel(form.discovery_type)}
            </p>
          </div>
          <div className="signal-card neutral" style={{ marginTop: 12 }}>
            <strong>当前草稿</strong>
            <p className="muted">
              {tool ? `${tool.name} / ${statusLabel(tool.status)}` : "尚未保存"}
              <br />
              上游：{form.base_url || "-"}
              <br />
              接口：{endpoints.length ? `${endpoints.length} 个` : "尚未导入"}
            </p>
          </div>
        </aside>

        <section className="panel">
          <div className="toolbar">
            <div>
              <h2>{stepTitle}</h2>
              <span className="muted">第 {currentStepIndex + 1} / {steps.length} 步</span>
            </div>
            <span className={busy ? "status info" : "status good"}>{busy || "就绪"}</span>
          </div>

          {renderStepContent()}

          <p className={`form-message ${message ? "success" : ""}`}>{message || error}</p>
          <div className="toolbar-actions">
            {currentStepIndex > 0 && (
              <button className="button secondary" type="button" onClick={() => setCurrentStepIndex((current) => current - 1)} disabled={Boolean(busy)}>
                <ChevronLeft size={16} />
                上一步
              </button>
            )}
            {currentStepIndex < steps.length - 1 ? (
              <button className="button" type="button" onClick={() => void goNext()} disabled={Boolean(busy)}>
                {actionLabel}
                <ChevronRight size={16} />
              </button>
            ) : (
              <>
                <button className="button" type="button" onClick={() => void bulkPublish()} disabled={Boolean(busy) || !endpoints.length}>
                  <Rocket size={16} />
                  启用并发布全部放行接口
                </button>
                <button className="button secondary" type="button" onClick={() => void finishAsDraft()} disabled={Boolean(busy)}>
                  保存并进入工作台
                </button>
              </>
            )}
            <button className="button secondary" type="button" onClick={() => router.push("/admin/tools")} disabled={Boolean(busy)}>
              返回工具列表
            </button>
          </div>
        </section>
      </div>
    </PortalShell>
  );
}
