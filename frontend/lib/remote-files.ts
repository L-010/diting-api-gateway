export type RemoteFile = {
  id: string;
  tool_id: string;
  tool_slug: string;
  tool_name: string;
  owner_user_id: string;
  owner_username: string;
  file_name: string;
  role: "input" | "intermediate" | "output" | "log" | "archive";
  visibility: "user" | "admin" | "internal";
  status: "pending" | "ready" | "delete_pending" | "deleted" | "expired" | "missing" | "quarantined" | "error";
  content_type: string;
  size_bytes: number | null;
  sha256: string;
  is_bundle: boolean;
  source_request_id: string | null;
  parent_resource_id: string | null;
  parent_kind: string | null;
  parent_platform_id: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  downloadable: boolean;
  deletable: boolean;
};

export type RemoteFilePage = {
  items: RemoteFile[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
  used_bytes: number;
  quota_bytes: number;
};

export function fileRoleLabel(value: string) {
  return ({ input: "输入", intermediate: "过程", output: "结果", log: "日志", archive: "归档" } as Record<string, string>)[value] || value;
}

export function fileStatusLabel(value: string) {
  return ({ pending: "同步中", ready: "可下载", delete_pending: "删除中", deleted: "已删除", expired: "已过期", missing: "上游已清理", quarantined: "已隔离", error: "异常" } as Record<string, string>)[value] || value;
}

export function fileStatusTone(value: string) {
  if (value === "ready") return "status good";
  if (["pending", "delete_pending"].includes(value)) return "status info";
  if (["expired", "missing", "deleted"].includes(value)) return "status warn";
  return "status danger";
}
