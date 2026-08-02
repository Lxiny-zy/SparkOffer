import { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Home, User, BookOpen, GitFork, Clock, Star, BriefcaseBusiness, Code2,
  Sun, Moon, LogOut, Menu, X, ChevronLeft, ChevronRight, Settings, MessageSquare, BarChart3,
  Brain, LucideIcon,
} from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "@/lib/utils";
import CatAvatar from "./CatAvatar";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: "训练",
    items: [
      { path: "/", label: "我的基地", icon: Home },
      { path: "/algorithm", label: "算法竞技场", icon: Code2 },
      { path: "/qa-arena", label: "问答演练场", icon: MessageSquare },
      { path: "/knowledge-training", label: "知识训练场", icon: Brain },
      { path: "/job-prep", label: "定向备战", icon: BriefcaseBusiness },
    ],
  },
  {
    label: "数据",
    items: [
      { path: "/profile", label: "成长报告", icon: User },
      { path: "/graph", label: "能力星图", icon: GitFork },
      { path: "/history", label: "时光机", icon: Clock },
      { path: "/favorites", label: "宝藏夹", icon: Star },
      { path: "/rag-dashboard", label: "RAG 仪表盘", icon: BarChart3 },
    ],
  },
  {
    label: "工具",
    items: [
      { path: "/knowledge", label: "知识库", icon: BookOpen },
      { path: "/settings", label: "设置", icon: Settings },
    ],
  },
];

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") || "dark");
  const [openForLocation, setOpenForLocation] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar-collapsed") === "true");
  const open = openForLocation === location.key;
  const drawerRef = useRef<HTMLDivElement>(null);

  // Mobile drawer is modal: close on Escape, move focus into it on open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenForLocation(null);
    };
    document.addEventListener("keydown", onKey);
    drawerRef.current?.querySelector("button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("sidebar-collapsed", String(collapsed));
  }, [collapsed]);

  const toggleTheme = () => setTheme((t: string) => t === "dark" ? "light" : "dark");
  const isActive = (path: string) =>
    path === "/" ? location.pathname === "/" : location.pathname.startsWith(path);

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  const renderNavItem = ({ path, label, icon: Icon }: NavItem) => {
    const active = isActive(path);
    const btn = (
      <button
        onClick={() => {
          setOpenForLocation(null);
          navigate(path);
        }}
        className={cn(
          "sig-navitem active:scale-[0.97]",
          active && !collapsed && "sig-navitem-active",
          collapsed && "justify-center px-0"
        )}
      >
        <Icon
          size={18}
          className={cn("shrink-0 transition-colors", active && "text-[color:var(--sig-accent)]")}
        />
        {!collapsed && <span className="truncate">{label}</span>}
      </button>
    );

    if (collapsed) {
      return (
        <Tooltip key={path} delayDuration={0}>
          <TooltipTrigger asChild>
            <div className="flex flex-col items-center relative">
              {btn}
              {active && <div className="w-5 h-0.5 mt-1" style={{ background: "var(--sig-accent)" }} />}
            </div>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={8}>{label}</TooltipContent>
        </Tooltip>
      );
    }
    return <div key={path}>{btn}</div>;
  };

  const renderGroup = (group: NavGroup, index: number) => (
    <div key={group.label} className={cn("mb-1", !collapsed && "mb-4")}>
      {!collapsed && (
        <div className="px-3 mb-2 flex items-center justify-between sig-kicker">
          <span>{group.label}</span>
          <span className="sig-num text-[9px] text-[color:var(--sig-faint)]">0{index + 1}</span>
        </div>
      )}
      {collapsed && <div className="h-2" />}
      <div className="flex flex-col gap-0.5">
        {group.items.map(renderNavItem)}
      </div>
    </div>
  );

  const nav = (
    <aside
      className={cn(
        "sig-sidebar flex flex-col h-full relative transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] overflow-hidden border-r",
        collapsed ? "w-[72px]" : "w-[260px]"
      )}
      data-collapsed={collapsed ? "true" : "false"}
      style={{ background: "var(--sig-bg)", borderColor: "var(--sig-line)" }}
    >
      {/* Brand */}
      <div className={cn("sig-sidebar-brand flex items-center shrink-0 px-4 py-5 relative z-10", collapsed ? "justify-center" : "gap-2.5")}>
        <CatAvatar size={collapsed ? 28 : 32} mood="idle" className="shrink-0" />
        {!collapsed && (
          <div className="min-w-0">
            <span className="sig-display block text-lg tracking-[-0.02em]">SparkOffer</span>
            <span className="sig-mono block mt-1 text-[8px] tracking-[0.2em] text-[color:var(--sig-faint)]">INTERVIEW / OS</span>
          </div>
        )}
      </div>

      <div className="sig-hr" />

      <TooltipProvider delayDuration={0}>
        <nav className={cn("flex-1 flex flex-col overflow-y-auto py-3 relative z-10", collapsed ? "px-2" : "px-3")}>
          {NAV_GROUPS.map(renderGroup)}
        </nav>

        <div className="sig-hr" />

        {/* Bottom section */}
        <div className={cn("py-3 space-y-0.5 relative z-10", collapsed ? "px-2" : "px-3")}>
          {/* User card */}
          {user && !collapsed && (
            <div className="sig-card sig-user-card mb-2 px-3 py-2.5 flex items-center gap-2.5">
              <div
                className="w-8 h-8 rounded flex items-center justify-center text-xs font-semibold shrink-0"
                style={{ background: "var(--sig-accent)", color: "var(--sig-accent-fg)" }}
              >
                {(user.name || user.email).slice(0, 1).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-text truncate flex items-center gap-1.5">
                  {user.name || user.email.split("@")[0]}
                  <span className="status-dot shrink-0" />
                </div>
                <div className="text-[10px] text-dim truncate">{user.email}</div>
              </div>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button onClick={handleLogout} className="text-muted-fg hover:text-[color:var(--sig-danger)] transition-colors p-1 rounded">
                    <LogOut size={14} />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top">退出登录</TooltipContent>
              </Tooltip>
            </div>
          )}
          {user && collapsed && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button onClick={handleLogout} className="sig-navitem justify-center px-0 hover:text-[color:var(--sig-danger)]">
                  <LogOut size={18} />
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" sideOffset={8}>退出登录</TooltipContent>
            </Tooltip>
          )}

          <Tooltip>
            <TooltipTrigger asChild>
              <button onClick={toggleTheme} className={cn("sig-navitem", collapsed && "justify-center px-0")}>
                {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
                {!collapsed && (theme === "dark" ? "浅色模式" : "深色模式")}
              </button>
            </TooltipTrigger>
            {collapsed && <TooltipContent side="right" sideOffset={8}>{theme === "dark" ? "浅色模式" : "深色模式"}</TooltipContent>}
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <button onClick={() => setCollapsed((c: boolean) => !c)} className={cn("sig-navitem group", collapsed && "justify-center px-0")}>
                <span className="transition-transform duration-300 group-hover:translate-x-0.5">
                  {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
                </span>
                {!collapsed && "收起侧栏"}
              </button>
            </TooltipTrigger>
            {collapsed && <TooltipContent side="right" sideOffset={8}>展开侧栏</TooltipContent>}
          </Tooltip>
        </div>
      </TooltipProvider>
    </aside>
  );

  return (
    <>
      <div
        className="sig-mobile-header md:hidden flex items-center justify-between px-4 py-3 shrink-0 border-b"
        style={{ background: "var(--sig-bg)", borderColor: "var(--sig-line)" }}
      >
        <div
          className="flex items-center gap-2.5 cursor-pointer"
          onClick={() => {
            setOpenForLocation(null);
            navigate("/");
          }}
        >
          <CatAvatar size={28} mood="static" />
          <span className="sig-display text-base tracking-[-0.02em]">SparkOffer</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setOpenForLocation((current) => current === location.key ? null : location.key)}
          aria-label={open ? "关闭导航" : "打开导航"}
        >
          {open ? <X size={18} /> : <Menu size={18} />}
        </Button>
      </div>

      <div className="hidden md:flex shrink-0">{nav}</div>

      {open && (
        <div
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-label="站点导航"
          className="fixed inset-0 z-50 md:hidden flex"
        >
          <div className="animate-slide-in-left">{nav}</div>
          <div className="flex-1 animate-fade-in" style={{ background: "var(--sig-overlay)" }} onClick={() => setOpenForLocation(null)} aria-hidden="true" />
        </div>
      )}
    </>
  );
}
