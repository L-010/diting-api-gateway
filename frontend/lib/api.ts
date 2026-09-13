export type ApiError = { status?: number; code?: string; message?: string; request_id?: string; detail?: unknown; details?: unknown };

function csrfToken(): string {
  return (
    document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith("agw_csrf="))
      ?.split("=")[1] ?? ""
  );
}

export async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", csrfToken());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(url, { ...init, headers, credentials: "include" });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("api-gateway:unauthorized"));
  }
  if (!response.ok) throw { ...(data && typeof data === "object" ? data : {}), status: response.status } as ApiError;
  return data as T;
}

export function isApiErrorStatus(error: unknown, status: number): boolean {
  return Boolean(error && typeof error === "object" && (error as ApiError).status === status);
}

export function errorMessage(error: unknown): string {
  if (error && typeof error === "object") {
    const candidate = error as ApiError;
    if (candidate.message) return candidate.request_id ? `${candidate.message}（request_id: ${candidate.request_id}）` : candidate.message;
    if (candidate.detail) {
      if (typeof candidate.detail === "string") return candidate.detail;
      return JSON.stringify(candidate.detail);
    }
  }
  return "请求失败，请稍后重试。";
}
