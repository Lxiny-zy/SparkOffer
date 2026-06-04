import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Home, User, BookOpen, GitFork, Clock, Star, Mic, BriefcaseBusiness, Code2,
  Sun, Moon, LogOut, Menu, X, ChevronLeft, ChevronRight, Settings, MessageSquare, BarChart3,
  LucideIcon,
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
      { path: "/job-prep", label: "定向备战", icon: BriefcaseBusiness },
      { path: "/recording", label: "回放实验室", icon: Mic },
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
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => { setOpen(false); }, [location.pathname]);

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
        onClick={() => navigate(path)}
        className={cn(
          "flex items-center gap-3 w-full px-3 py-2.5 rounded-2xl text-[13px] font-medium transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] text-left group relative active:scale-[0.97]",
          active
            ? "glass-subtle text-text shadow-[0_2px_12px_var(--glow-primary)]"
            : "text-muted-fg hover:text-text hover:bg-primary/8",
          collapsed && "justify-center px-0 rounded-2xl"
        )}
      >
        {active && !collapsed && <span className="nav-active-indicator" />}
        <Icon
          size={18}
          className={cn(
            "shrink-0 transition-all duration-300",
            active ? "text-primary scale-110 drop-shadow-[0_0_6px_var(--tech-glow)]" : "text-muted-fg group-hover:text-text"
          )}
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
              {active && <div className="w-6 h-0.5 rounded-full mt-1" style={{ background: "linear-gradient(90deg, var(--aurora-1), var(--aurora-2))" }} />}
            </div>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={8}>{label}</TooltipContent>
        </Tooltip>
      );
    }
    return <div key={path}>{btn}</div>;
  };

  const renderGroup = (group: NavGroup) => (
    <div key={group.label} className={cn("mb-1", !collapsed && "mb-3")}>
      {!collapsed && (
        <div className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-fg/60">
          {group.label}
        </div>
      )}
      {collapsed && <div className="h-2" />}
      <div className="flex flex-col gap-0.5">
        {group.items.map(renderNavItem)}
      </div>
    </div>
  );

  const nav = (
    <aside className={cn(
      "flex flex-col h-full bg-sidebar relative transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] overflow-hidden",
      collapsed ? "w-[72px]" : "w-[260px]"
    )}>
      {/* Subtle aurora glow at top */}
      <div className="absolute top-0 left-0 right-0 h-32 pointer-events-none opacity-40" style={{ background: "radial-gradient(ellipse at top, var(--aurora-2), transparent 70%)" }} />

      {/* Brand */}
      <div className={cn("flex items-center shrink-0 px-4 py-5 relative z-10", collapsed ? "justify-center" : "gap-2.5")}>
        <CatAvatar size={collapsed ? 28 : 32} mood="idle" className="shrink-0" />
        {!collapsed && (
          <span className="text-lg font-display font-bold gradient-text-vivid">SparkOffer</span>
        )}
      </div>

      <div className="divider-gradient mx-4" />

      <TooltipProvider delayDuration={0}>
        <nav className={cn("flex-1 flex flex-col overflow-y-auto py-3 relative z-10", collapsed ? "px-2" : "px-3")}>
          {NAV_GROUPS.map(renderGroup)}
        </nav>

      <div className="divider-gradient mx-4" />

        {/* Bottom section */}
        <div className={cn("py-3 space-y-0.5 relative z-10", collapsed ? "px-2" : "px-3")}>
          {/* User card */}
          {user && !collapsed && (
            <div className="mb-2 px-3 py-2.5 rounded-2xl glass-subtle flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold text-white shrink-0" style={{ background: "linear-gradient(135deg, var(--aurora-1), var(--aurora-2))" }}>
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
                  <button onClick={handleLogout} className="text-muted-fg hover:text-red transition-colors p-1 rounded-lg hover:bg-red/10">
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
                <button onClick={handleLogout} className="flex items-center justify-center w-full py-2.5 rounded-2xl text-muted-fg hover:text-red hover:bg-red/10 transition-all duration-300 active:scale-[0.97]">
                  <LogOut size={18} />
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" sideOffset={8}>退出登录</TooltipContent>
            </Tooltip>
          )}

          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={toggleTheme}
                className={cn(
                  "flex items-center gap-3 w-full py-2.5 px-3 rounded-2xl text-[13px] font-medium text-muted-fg hover:text-text hover:bg-primary/8 transition-all duration-300 active:scale-[0.97]",
                  collapsed && "justify-center px-0"
                )}
              >
                {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
                {!collapsed && (theme === "dark" ? "浅色模式" : "深色模式")}
              </button>
            </TooltipTrigger>
            {collapsed && <TooltipContent side="right" sideOffset={8}>{theme === "dark" ? "浅色模式" : "深色模式"}</TooltipContent>}
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => setCollapsed((c: boolean) => !c)}
                className={cn(
                  "flex items-center gap-3 w-full py-2.5 px-3 rounded-2xl text-[13px] font-medium text-muted-fg hover:text-text hover:bg-primary/8 transition-all duration-300 active:scale-[0.97] group",
                  collapsed && "justify-center px-0"
                )}
              >
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
      <div className="md:hidden flex items-center justify-between px-4 py-3 bg-card/80 backdrop-blur-md border-b border-border shrink-0">
        <div className="flex items-center gap-2.5 cursor-pointer" onClick={() => navigate("/")}>
          <CatAvatar size={28} mood="static" />
          <span className="text-base font-display font-bold aurora-text">SparkOffer</span>
        </div>
        <Button variant="ghost" size="icon" onClick={() => setOpen((o: boolean) => !o)}>
          {open ? <X size={18} /> : <Menu size={18} />}
        </Button>
      </div>

      <div className="hidden md:flex shrink-0">{nav}</div>

      {open && (
        <div className="fixed inset-0 z-50 md:hidden flex">
          <div className="animate-fade-in">{nav}</div>
          <div className="flex-1 bg-black/50 backdrop-blur-sm" onClick={() => setOpen(false)} />
        </div>
      )}
    </>
  );
}
