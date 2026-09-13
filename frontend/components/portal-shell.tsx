"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Bell,
  ClipboardList,
  FolderArchive,
  KeyRound,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PanelsTopLeft,
  ShieldCheck,
  Settings,
  Sun,
  UserRound,
  Wrench,
  X,
} from "lucide-react";
import { isValidElement, ReactNode, useEffect, useRef, useState } from "react";
import { api, errorMessage, isApiErrorStatus } from "@/lib/api";
import { clearAdminToolsCache, loadAdminTools } from "@/lib/admin-tools-cache";
import { notifyToast } from "@/lib/toast";

const THEME_STORAGE_KEY = "api-gateway:workspace-theme";
const SIDEBAR_STORAGE_KEY = "api-gateway:sidebar-collapsed";
const CURRENT_USER_CACHE_TTL = 60_000;
const UNREAD_COUNT_CACHE_TTL = 30_000;

type WorkspaceTheme = "light" | "dark";

type CurrentUser = {
  username: string;
  display_name?: string | null;
  roles: string[];
  approval_status?: string;
  must_change_password: boolean;
};
type SiteConfig = { site_name: string; site_subtitle: string; brand_image_url: string };

type PortalRuntimeCache = {
  currentUser: { value: CurrentUser; cachedAt: number } | null;
  currentUserRequest: Promise<CurrentUser> | null;
  unreadCount: { value: number; cachedAt: number } | null;
  unreadCountRequest: Promise<number> | null;
  pendingNavigationPath: string | null;
  prefetchedRoutes: Set<string>;
};

declare global {
  interface Window {
    __apiGatewayPortalRuntime?: PortalRuntimeCache;
  }
}

const serverRuntimeCache: PortalRuntimeCache = {
  currentUser: null,
  currentUserRequest: null,
  unreadCount: null,
  unreadCountRequest: null,
  pendingNavigationPath: null,
  prefetchedRoutes: new Set<string>(),
};

function runtimeCache(): PortalRuntimeCache {
  if (typeof window === "undefined") return serverRuntimeCache;
  window.__apiGatewayPortalRuntime ??= {
    currentUser: null,
    currentUserRequest: null,
    unreadCount: null,
    unreadCountRequest: null,
    pendingNavigationPath: null,
    prefetchedRoutes: new Set<string>(),
  };
  return window.__apiGatewayPortalRuntime;
}

function initialTheme(): WorkspaceTheme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.workspaceTheme === "dark" ? "dark" : "light";
}

function initialSidebarCollapsed(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.dataset.workspaceSidebar === "collapsed";
}

function loadCurrentUser(force = false): Promise<CurrentUser> {
  const cache = runtimeCache();
  const now = Date.now();
  if (!force && cache.currentUser && now - cache.currentUser.cachedAt < CURRENT_USER_CACHE_TTL) {
    return Promise.resolve(cache.currentUser.value);
  }
  if (cache.currentUserRequest) return cache.currentUserRequest;
  cache.currentUserRequest = api<CurrentUser>("/api/me")
    .then((current) => {
      cache.currentUser = { value: current, cachedAt: Date.now() };
      return current;
    })
    .finally(() => {
      cache.currentUserRequest = null;
    });
  return cache.currentUserRequest;
}

function loadUnreadCount(force = false): Promise<number> {
  const cache = runtimeCache();
  const now = Date.now();
  if (!force && cache.unreadCount && now - cache.unreadCount.cachedAt < UNREAD_COUNT_CACHE_TTL) {
    return Promise.resolve(cache.unreadCount.value);
  }
  if (cache.unreadCountRequest) return cache.unreadCountRequest;
  cache.unreadCountRequest = api<{ unread_count: number }>("/api/me/notifications?page=1&page_size=10")
    .then((payload) => {
      cache.unreadCount = { value: payload.unread_count, cachedAt: Date.now() };
      return payload.unread_count;
    })
    .finally(() => {
      cache.unreadCountRequest = null;
    });
  return cache.unreadCountRequest;
}

const userNav = [
  ["/portal", "使用概览", LayoutDashboard],
  ["/tools", "工具与接口", PanelsTopLeft],
  ["/api-keys", "API 密钥", KeyRound],
  ["/calls", "调用记录", ClipboardList],
  ["/tasks", "任务中心", ListTodo],
  ["/files", "文件与制品", FolderArchive],
  ["/notifications", "通知", Bell],
  ["/account", "账户设置", UserRound],
] as const;

const adminNav = [
  ["/admin", "管理总览", LayoutDashboard],
  ["/admin/tools", "工具管理", Wrench],
  ["/admin/users", "用户管理", ShieldCheck],
  ["/admin/files", "任务与文件", FolderArchive],
  ["/admin/monitor", "监控告警", Activity],
  ["/admin/audit", "审计日志", ClipboardList],
  ["/admin/settings", "平台设置", Settings],
] as const;

function isActivePath(pathname: string, href: string) {
  const cleanHref = href.split("?")[0].split("#")[0];
  if (cleanHref === "/admin") return pathname === cleanHref;
  return pathname === cleanHref || pathname.startsWith(`${cleanHref}/`);
}

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

function findPageDescription(node: ReactNode): string {
  if (Array.isArray(node)) {
    for (const child of node) {
      const description = findPageDescription(child);
      if (description) return description;
    }
    return "";
  }
  if (!isValidElement<{ className?: string; children?: ReactNode }>(node)) return "";
  const className = node.props.className || "";
  if (className.split(" ").includes("page-intro")) return nodeText(node.props.children).trim();
  return findPageDescription(node.props.children);
}

export function PortalShell({
  admin = false,
  title,
  description,
  headerActions,
  children,
}: {
  admin?: boolean;
  title: string;
  description?: string;
  headerActions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(() => runtimeCache().currentUser?.value ?? null);
  const [siteConfig, setSiteConfig] = useState<SiteConfig>({ site_name: "API Gateway", site_subtitle: admin ? "平台管理工作台" : "开发者统一工作台", brand_image_url: "" });
  const [loggingOut, setLoggingOut] = useState(false);
  const [guardError, setGuardError] = useState("");
  const [guardAttempt, setGuardAttempt] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(false);
  const [unreadCount, setUnreadCount] = useState(() => runtimeCache().unreadCount?.value ?? 0);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(initialSidebarCollapsed);
  const [theme, setTheme] = useState<WorkspaceTheme>(initialTheme);
  const [navigatingPath, setNavigatingPath] = useState<string | null>(() => runtimeCache().pendingNavigationPath);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const workspaceScrollRef = useRef<HTMLDivElement>(null);
  const shouldRestoreMenuFocus = useRef(false);

  useEffect(() => {
    api<SiteConfig>("/api/public/config").then(setSiteConfig).catch(() => undefined);
  }, []);

  useEffect(() => {
    let active = true;
    setGuardError("");
    loadCurrentUser(guardAttempt > 0)
      .then((current) => {
        if (!active) return;
        if (current.must_change_password) {
          router.replace("/change-password");
          return;
        }
        if (admin && !current.roles.includes("admin")) {
          router.replace("/portal");
          return;
        }
        setUser(current);
        if (!admin) {
          loadUnreadCount(guardAttempt > 0)
            .then((count) => {
              if (active) setUnreadCount(count);
            })
            .catch(() => undefined);
        }
      })
      .catch((cause) => {
        if (!active) return;
        runtimeCache().currentUser = null;
        if (isApiErrorStatus(cause, 401)) {
          clearAdminToolsCache();
          router.replace(`/login?next=${encodeURIComponent(pathname || "/portal")}`);
          return;
        }
        setGuardError(isApiErrorStatus(cause, 403) ? "当前账号无权访问此页面。" : errorMessage(cause));
      });
    return () => {
      active = false;
    };
  }, [admin, guardAttempt, pathname, router]);

  useEffect(() => {
    setMenuOpen(false);
    const cache = runtimeCache();
    if (!cache.pendingNavigationPath || !isActivePath(pathname, cache.pendingNavigationPath)) return;
    const frame = window.requestAnimationFrame(() => {
      cache.pendingNavigationPath = null;
      setNavigatingPath(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pathname]);

  useEffect(() => {
    const routes = (admin ? adminNav : userNav).map(([href]) => href);
    const cache = runtimeCache();
    const prefetch = () => {
      routes.forEach((href) => {
        if (cache.prefetchedRoutes.has(href)) return;
        cache.prefetchedRoutes.add(href);
        router.prefetch(href);
      });
      if (admin) void loadAdminTools().catch(() => undefined);
    };
    if (typeof window.requestIdleCallback === "function") {
      const idleId = window.requestIdleCallback(prefetch, { timeout: 1_500 });
      return () => window.cancelIdleCallback(idleId);
    }
    const timeoutId = globalThis.setTimeout(prefetch, 250);
    return () => globalThis.clearTimeout(timeoutId);
  }, [admin, router]);

  useEffect(() => {
    if (!navigatingPath) return;
    const timeoutId = window.setTimeout(() => {
      runtimeCache().pendingNavigationPath = null;
      setNavigatingPath(null);
    }, 5_000);
    return () => window.clearTimeout(timeoutId);
  }, [navigatingPath]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const syncMobileNavigation = () => setMobileNavigation(media.matches);
    syncMobileNavigation();
    media.addEventListener("change", syncMobileNavigation);
    return () => media.removeEventListener("change", syncMobileNavigation);
  }, []);

  useEffect(() => {
    function handleUnauthorized() {
      const cache = runtimeCache();
      clearAdminToolsCache();
      cache.currentUser = null;
      cache.unreadCount = null;
      cache.currentUserRequest = null;
      cache.unreadCountRequest = null;
      router.replace(`/login?next=${encodeURIComponent(pathname || (admin ? "/admin" : "/portal"))}`);
    }
    window.addEventListener("api-gateway:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("api-gateway:unauthorized", handleUnauthorized);
  }, [admin, pathname, router]);

  useEffect(() => {
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    const resolvedTheme: WorkspaceTheme = savedTheme === "dark" || savedTheme === "light"
      ? savedTheme
      : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    setTheme(resolvedTheme);
    document.documentElement.dataset.workspaceTheme = resolvedTheme;
    const savedSidebarCollapsed = window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
    setSidebarCollapsed(savedSidebarCollapsed);
    document.documentElement.dataset.workspaceSidebar = savedSidebarCollapsed ? "collapsed" : "expanded";

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    function syncSystemTheme(event: MediaQueryListEvent) {
      if (window.localStorage.getItem(THEME_STORAGE_KEY)) return;
      const nextTheme = event.matches ? "dark" : "light";
      setTheme(nextTheme);
      document.documentElement.dataset.workspaceTheme = nextTheme;
    }
    media.addEventListener("change", syncSystemTheme);
    return () => media.removeEventListener("change", syncSystemTheme);
  }, []);

  useEffect(() => {
    if (!menuOpen) {
      if (shouldRestoreMenuFocus.current) {
        shouldRestoreMenuFocus.current = false;
        menuButtonRef.current?.focus();
      }
      return;
    }

    shouldRestoreMenuFocus.current = true;
    const previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const sidebar = sidebarRef.current;
    const focusable = Array.from(
      sidebar?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [],
    );
    focusable[0]?.focus();

    function handleKeydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        return;
      }
      if (event.key !== "Tab" || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", handleKeydown);
    return () => {
      document.body.style.overflow = previousBodyOverflow;
      window.removeEventListener("keydown", handleKeydown);
    };
  }, [menuOpen]);

  useEffect(() => {
    const scroller = workspaceScrollRef.current;
    if (!scroller) return;
    const scrollContainer = scroller;
    let frame = 0;

    function syncTableHeaders() {
      frame = 0;
      const scrollerTop = scrollContainer.getBoundingClientRect().top;
      scrollContainer.querySelectorAll<HTMLElement>(".table-wrap").forEach((container) => {
        const header = container.querySelector<HTMLElement>("thead");
        if (!header) return;
        const containerRect = container.getBoundingClientRect();
        const headerHeight = header.getBoundingClientRect().height;
        const maxOffset = Math.max(0, containerRect.height - headerHeight);
        const offset = Math.max(0, Math.min(scrollerTop - containerRect.top, maxOffset));
        header.style.setProperty("--table-header-offset", `${offset}px`);
      });
    }

    function scheduleSync() {
      if (frame) return;
      frame = window.requestAnimationFrame(syncTableHeaders);
    }

    const observer = new MutationObserver(scheduleSync);
    scheduleSync();
    observer.observe(scrollContainer, { childList: true, subtree: true });
    scrollContainer.addEventListener("scroll", scheduleSync, { passive: true });
    window.addEventListener("resize", scheduleSync);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      observer.disconnect();
      scrollContainer.removeEventListener("scroll", scheduleSync);
      window.removeEventListener("resize", scheduleSync);
      scrollContainer.querySelectorAll<HTMLElement>("thead").forEach((header) => header.style.removeProperty("--table-header-offset"));
    };
  }, [pathname, user?.username]);

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await api("/api/auth/logout", { method: "POST" });
      clearAdminToolsCache();
      const cache = runtimeCache();
      cache.currentUser = null;
      cache.unreadCount = null;
      cache.currentUserRequest = null;
      cache.unreadCountRequest = null;
      router.replace("/login");
      router.refresh();
    } catch (cause) {
      notifyToast({ type: "error", message: "退出失败，会话仍保持登录状态", details: errorMessage(cause) });
      setLoggingOut(false);
    }
  }

  function toggleTheme() {
    const nextTheme: WorkspaceTheme = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    document.documentElement.dataset.workspaceTheme = nextTheme;
    window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  }

  function toggleSidebar() {
    setSidebarCollapsed((current) => {
      const nextValue = !current;
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(nextValue));
      document.documentElement.dataset.workspaceSidebar = nextValue ? "collapsed" : "expanded";
      return nextValue;
    });
  }

  function beginNavigation(event: React.MouseEvent<HTMLAnchorElement>, href: string) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (isActivePath(pathname, href)) return;
    runtimeCache().pendingNavigationPath = href;
    setNavigatingPath(href);
  }

  if (!user) {
    return (
      <main className="route-guard" role={guardError ? "alert" : "status"}>
        <div className="guard-card">
          <strong>{guardError ? "暂时无法进入此页面" : "正在验证访问权限..."}</strong>
          {guardError && <><p>{guardError}</p><button className="button" type="button" onClick={() => setGuardAttempt((value) => value + 1)}>重试</button></>}
        </div>
      </main>
    );
  }

  const nav = admin ? adminNav : userNav;
  const resolvedDescription = description || findPageDescription(children);
  return (
    <div className={sidebarCollapsed ? "app-shell sidebar-collapsed" : "app-shell"} data-theme={theme}>
      {menuOpen && <button className="sidebar-backdrop" type="button" aria-label="关闭导航" onClick={() => setMenuOpen(false)} />}
      <aside
        ref={sidebarRef}
        className={menuOpen ? "sidebar open" : "sidebar"}
        aria-label="主导航"
        aria-hidden={mobileNavigation && !menuOpen ? true : undefined}
        inert={mobileNavigation && !menuOpen ? true : undefined}
      >
        <button className="sidebar-close icon-button" type="button" aria-label="关闭导航" onClick={() => setMenuOpen(false)}><X size={18} /></button>
        <Link href={admin ? "/admin" : "/portal"} className="brand-block" title={siteConfig.site_name}>
          {siteConfig.brand_image_url ? <img className="brand-image" src={siteConfig.brand_image_url} alt="" /> : <span className="brand-mark">G</span>}
          <span className="brand-copy">{siteConfig.site_name}</span>
        </Link>
        <p className="sidebar-subtitle">{siteConfig.site_subtitle}</p>
        <nav className="side-nav">
          {nav.map(([href, label, Icon]) => {
            const active = isActivePath(pathname, href);
            return (
              <Link key={href} href={href} prefetch className={active ? "nav-item active" : "nav-item"} aria-current={active ? "page" : undefined} aria-label={label} title={sidebarCollapsed ? label : undefined} onClick={(event) => beginNavigation(event, href)}>
                <Icon size={18} />
                <span>{label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-rule" />
        <p className="sidebar-note">
          {admin
            ? "管理工具、用户、运行监控和审计日志。工具接口治理统一在对应工具工作台完成。"
            : "从工具目录选择接口，通过统一 Gateway 调用。"}
        </p>
        <div className="sidebar-controls" aria-label="外观设置">
          <button type="button" className="sidebar-control" onClick={toggleTheme} aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"} title={theme === "dark" ? "浅色模式" : "深色模式"}>
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            <span>{theme === "dark" ? "浅色模式" : "深色模式"}</span>
          </button>
          <button type="button" className="sidebar-control desktop-collapse-button" onClick={toggleSidebar} aria-expanded={!sidebarCollapsed} aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"} title={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}>
            {sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            <span>{sidebarCollapsed ? "展开" : "收起"}</span>
          </button>
        </div>
      </aside>
      <main className={navigatingPath ? "workspace route-changing" : "workspace"} aria-busy={navigatingPath ? true : undefined}>
        <span className="route-progress" aria-hidden="true" />
        <header className="topbar">
          <div className="topbar-heading">
            <button ref={menuButtonRef} className="mobile-menu-button icon-button" type="button" aria-label="打开导航" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}><Menu size={19} /></button>
            <div className="page-heading-copy">
              <h1>{title}</h1>
              {resolvedDescription && <p>{resolvedDescription}</p>}
            </div>
          </div>
          <div className="topbar-tools">
            {headerActions}
            {!admin && <Link className="notification-button icon-button" href="/notifications" aria-label={unreadCount ? `${unreadCount} 条未读通知` : "通知"} title="通知"><Bell size={17} />{unreadCount > 0 && <span>{Math.min(unreadCount, 99)}</span>}</Link>}
            <span className="role-label">{admin ? "管理员" : "Gateway 用户"}</span>
            <span className="user-summary">
              {user?.display_name || user?.username || "加载中"} / {admin ? "平台运维" : "开发者"}
            </span>
            <button type="button" className="icon-text-button" aria-label="退出登录" title="退出登录" onClick={logout} disabled={loggingOut} aria-busy={loggingOut}>
              <LogOut size={16} />
              <span>{loggingOut ? "退出中" : "退出"}</span>
            </button>
          </div>
        </header>
        <section className="workspace-body">
          <div ref={workspaceScrollRef} className="workspace-scroll">{children}</div>
        </section>
      </main>
    </div>
  );
}

export function Metric({
  label,
  value,
  caption,
  tone = "blue",
}: {
  label: string;
  value: string;
  caption: string;
  tone?: "blue" | "green" | "purple" | "red" | "amber";
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{caption}</small>
    </article>
  );
}
