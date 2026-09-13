"use client";

import { ReactNode, useEffect, useRef, useState } from "react";
import { TOAST_EVENT, ToastPayload } from "@/lib/toast";

type ToastItem = Required<Pick<ToastPayload, "type" | "message">> & {
  id: number;
  details: string;
};

function normalizeToast(payload: ToastPayload): ToastItem {
  return {
    id: Date.now() + Math.random(),
    type: payload.type || "info",
    message: payload.message,
    details: payload.details || "",
  };
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    function onToast(event: Event) {
      const payload = (event as CustomEvent<ToastPayload>).detail;
      if (!payload?.message) return;
      const item = normalizeToast(payload);
      setItems((current) => [...current.slice(-3), item]);
      const timer = window.setTimeout(() => {
        setItems((current) => current.filter((row) => row.id !== item.id));
      }, payload.type === "error" ? 5200 : 3200);
      timers.current.push(timer);
    }

    window.addEventListener(TOAST_EVENT, onToast);
    return () => {
      window.removeEventListener(TOAST_EVENT, onToast);
      timers.current.forEach((timer) => window.clearTimeout(timer));
      timers.current = [];
    };
  }, []);

  return (
    <>
      {children}
      <div className="toast-region" role="status" aria-live="polite" aria-atomic="true">
        {items.map((item) => (
          <div className={`toast toast-${item.type}`} key={item.id}>
            <strong>{item.message}</strong>
            {item.details && <span>{item.details}</span>}
          </div>
        ))}
      </div>
    </>
  );
}
