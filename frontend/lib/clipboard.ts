import { notifyToast } from "@/lib/toast";

function legacyCopy(value: string) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!ok) throw new Error("复制失败");
}

export async function copyToClipboard(value: string, message = "已复制到剪贴板") {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      legacyCopy(value);
    }
    notifyToast({ type: "success", message });
  } catch (error) {
    notifyToast({ type: "error", message: "复制失败，请手动选择内容复制" });
    throw error;
  }
}
