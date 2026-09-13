export type ToolView = {
  id: string;
  slug: string;
  name: string;
  description: string;
  status: string;
  is_enabled: boolean;
  base_url_masked: string;
  rate_limit_per_minute: number;
  auth_type: string;
  token_configured: boolean;
  token_label: string;
  health_path: string;
  environment_mode: string;
  environments: Array<Record<string, unknown>>;
  discovery_type: string;
  network_zone: string;
  verify_tls: boolean;
  retry_count: number;
  network_isolation_mode: string;
  network_isolation_note: string;
  network_isolation_confirmed: boolean;
  endpoint_count: number;
  published_count: number;
  draft_count: number;
  excluded_count: number;
  blocked_count: number;
  inconsistent_route_count: number;
  health?: { status?: string; reachable?: boolean; status_code?: number | null; path?: string } | null;
  latest_openapi_version?: string | null;
  latest_openapi_sha256?: string | null;
  latest_openapi_imported_at?: string | null;
  latest_import_summary?: Record<string, unknown> | null;
  onboarding: {
    steps?: Array<{ key: string; label: string; done: boolean; hint: string }>;
    completed_count?: number;
    total_count?: number;
    next_action?: string;
    pending_access_policy_count?: number;
    latest_batch_id?: string | null;
    latest_batch_status?: string | null;
    latest_batch_pending_count?: number;
    release_ready?: boolean;
  };
  task_adapter: Record<string, unknown>;
  file_retention_days: number;
  storage_quota_bytes: number;
  max_file_bytes: number;
  file_access_enabled: boolean;
  file_quarantined: boolean;
  storage_total_bytes: number | null;
  storage_free_bytes: number | null;
  storage_checked_at: string | null;
  storage_risk_acknowledged: boolean;
};

export type ToolConfiguration = {
  tool_id: string;
  slug: string;
  name: string;
  description: string;
  status: "draft" | "active" | "disabled";
  is_enabled: boolean;
  default_gateway_prefix: string;
  base_url: string;
  health_path: string;
  auth_type: "none" | "bearer" | "header_api_key";
  token_label: string;
  token_configured: boolean;
  rate_limit_per_minute: number;
  network_zone: string;
  verify_tls: boolean;
  retry_count: number;
  connect_timeout_seconds: number;
  request_timeout_seconds: number;
  network_isolation_mode: "firewall_allowlist" | "private_network" | "machine_credential_only";
  network_isolation_note: string;
  network_isolation_confirmed: boolean;
  file_retention_days: number;
  storage_quota_bytes: number;
  max_file_bytes: number;
  file_access_enabled: boolean;
  file_quarantined: boolean;
  storage_risk_acknowledged: boolean;
  config_revision: number;
  updated_at: string;
};

export type ToolConfigurationRevision = {
  id: string;
  config_revision: number;
  base_url_masked: string;
  status: string;
  changed_by_user_id: string | null;
  reason: string;
  created_at: string;
};

export type EndpointView = {
  id: string;
  method: string;
  upstream_path: string;
  gateway_path: string;
  operation_id: string | null;
  summary: string;
  content_types: string[];
  group_name: string;
  status: string;
  import_action: string;
  risk_level: string;
  risk_flags: string[];
  governance: Record<string, unknown>;
  parameters: unknown[];
  request_body: Record<string, unknown>;
  responses: Record<string, unknown>;
  exclusion_reason: string;
  public_description: string;
  route_policy?: {
    id: string;
    method: string;
    gateway_path: string;
    upstream_path: string;
    allowed_content_types: string[];
    max_request_bytes: number;
    max_response_bytes: number;
    request_timeout_seconds: number;
    allow_stream_upload: boolean;
    allow_stream_download: boolean;
    access_mode: AccessMode;
    resource_policy: ResourcePolicyRules;
    policy_version: number;
    require_idempotency_key: boolean;
    allow_retry: boolean;
    risk_level: string;
    route_version: number;
    policy?: Record<string, unknown>;
    storage_action?: string;
  } | null;
  access_policy: AccessPolicyView;
  published: boolean;
  enabled: boolean;
};

export type ImportBatchView = {
  id: string;
  tool_id: string;
  spec_id: string | null;
  source_type: string;
  source_url: string;
  source_sha256: string;
  openapi_version: string;
  total_operations: number;
  selected_operations: number;
  added_count: number;
  changed_count: number;
  unchanged_count: number;
  excluded_count: number;
  blocked_count: number;
  status: string;
  summary: Record<string, unknown>;
  governance_summary: Record<string, unknown>;
  created_at: string;
};

export type OpenApiDiffView = {
  id: string;
  batch_id: string;
  endpoint_id: string | null;
  method: string;
  upstream_path: string;
  gateway_path: string;
  summary: string;
  description: string;
  diff_type: string;
  risk_level: string;
  risk_flags: string[];
  decision: string;
  detail: Record<string, unknown>;
  access_policy_suggestion: AccessPolicySuggestion;
  access_policy: AccessPolicyView;
  access_policy_complete: boolean;
  access_policy_missing_fields: string[];
  created_at: string;
};

export type AccessMode = "authenticated" | "owner" | "shared" | "admin_only";

export type ResourcePolicyRule = {
  action: "require_owner" | "require_parent_owner" | "register_owner" | "mark_deleted";
  resource_kind: string;
  source: "path" | "query" | "request_json" | "response_json" | "response_header";
  selector?: string;
  selectors?: string[];
  optional?: boolean;
  many?: boolean;
  parent?: Record<string, unknown>;
};

export type ResourcePolicyRules = {
  version?: number;
  pre_checks?: ResourcePolicyRule[];
  post_actions?: ResourcePolicyRule[];
  list_filter?: Record<string, unknown> | null;
  requires_shared_confirmation?: boolean;
};

export type AccessPolicySuggestion = {
  access_mode: AccessMode;
  rules: ResourcePolicyRules;
  complete: boolean;
  missing_fields: string[];
  reason: string;
};

export type AccessPolicyView = {
  access_mode: AccessMode;
  rules: ResourcePolicyRules;
  source: string;
  confirmed: boolean;
  complete: boolean;
  missing_fields: string[];
  reason: string;
};

export function accessModeLabel(value: AccessMode | string) {
  const labels: Record<string, string> = {
    authenticated: "平台用户",
    owner: "创建者资源",
    shared: "共享数据",
    admin_only: "仅管理员",
  };
  return labels[value] ?? value;
}

export function statusLabel(value: string) {
  const labels: Record<string, string> = {
    draft: "草稿",
    active: "已启用",
    disabled: "已停用",
    candidate: "候选",
    published: "已发布",
    excluded: "已排除",
    blocked: "已阻断",
  };
  return labels[value] ?? value;
}

export function riskLabel(value: string) {
  const labels: Record<string, string> = {
    info: "信息",
    low: "低",
    medium: "中",
    high: "高",
    blocker: "阻断",
  };
  return labels[value] ?? value;
}

export function diffLabel(value: string) {
  const labels: Record<string, string> = {
    new: "新增",
    changed: "变更",
    unchanged: "无变化",
    deleted: "上游删除",
  };
  return labels[value] ?? value;
}
