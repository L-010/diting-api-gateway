export type GatewayCall = {
  request_id: string;
  user_id: string | null;
  username: string;
  api_key_id: string | null;
  api_key_prefix: string;
  tool_id: string | null;
  tool_slug: string;
  endpoint_id: string | null;
  endpoint_summary: string;
  operation_id: string | null;
  method: string;
  path: string;
  status_code: number;
  upstream_status_code: number | null;
  duration_ms: number;
  request_bytes: number;
  response_bytes: number;
  error_code: string | null;
  failure_stage: string;
  query: Array<{ key: string; value: string }>;
  created_at: string;
  upstream_path: string;
  client_ip_fingerprint: string;
  user_agent_fingerprint: string;
};

export type GatewayCallPage = {
  items: GatewayCall[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
};

export function formatBytes(value: number) {
  if (!value || value < 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let scaled = value;
  let unitIndex = 0;
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  return `${unitIndex === 0 ? Math.round(scaled) : Number(scaled.toFixed(1))} ${units[unitIndex]}`;
}

export function statusTone(value: number) {
  if (value < 400) return "status good";
  if (value < 500) return "status warn";
  return "status danger";
}

export function failureStageLabel(value: string) {
  return {
    completed: "执行完成",
    access_control: "访问控制",
    rate_limit: "平台限流",
    routing: "路由匹配",
    upstream: "上游执行",
    gateway: "Gateway 处理",
  }[value] || "未知阶段";
}
