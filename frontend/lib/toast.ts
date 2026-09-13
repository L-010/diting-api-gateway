export type ToastType = "success" | "error" | "info";

export type ToastPayload = {
  type?: ToastType;
  message: string;
  details?: string;
};

export const TOAST_EVENT = "api-gateway-toast";

export function notifyToast(payload: ToastPayload) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<ToastPayload>(TOAST_EVENT, { detail: payload }));
}
