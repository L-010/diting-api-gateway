import type { Metadata } from "next";
import { ToastProvider } from "@/components/toast-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "API Gateway 开发者平台",
  description: "注册申请、管理员审批、全局 API Key、统一 Gateway 调用与审计的本地 MVP",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var themeKey="api-gateway:workspace-theme";var savedTheme=localStorage.getItem(themeKey);var theme=savedTheme==="dark"||savedTheme==="light"?savedTheme:(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.workspaceTheme=theme;document.documentElement.dataset.workspaceSidebar=localStorage.getItem("api-gateway:sidebar-collapsed")==="true"?"collapsed":"expanded"}catch(e){}})();`,
          }}
        />
      </head>
      <body>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
