import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Home, User, BookOpen, GitFork, Clock, Star, Mic, BriefcaseBusiness, Code2,
  Sun, Moon, LogOut, Menu, X, ChevronLeft, ChevronRight, Settings, MessageSquare,
  LucideIcon,
} from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "@/lib/utils";
import CatAvatar from "./CatAvatar";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
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

const NAV_ITEMS: NavItem[] = [
  { path: "/", label: "我的基地", icon: Home },
  { path: "/profile", label: "成长报告", icon: User },
  { path: "/knowledge", label: "刷题馆", icon: BookOpen },
  { path: "/graph", label: "能力星图", icon: GitFork },
  { path: "/history", label: "时光机", icon: Clock },
  { path: "/favorites", label: "宝藏夹", icon: Star },
  { path: "/algorithm", label: "算法竞技场", icon: Code2 },
  { path: "/qa-arena", label: "问答演练场", icon: MessageSquare },
  { path: "/job-prep", label: "定向备战", icon: BriefcaseBusiness },
  { path: "/recording", label: "回放实验室", icon: Mic },
  { path: "/settings", label: "设置", icon: Settings },
];

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth()!;
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

  const navItem = ({ path, label, icon: Icon }: NavItem) => {
    const active = isActive(path);
    const btn = (
      <button
        onClick={() => navigate(path)}
        className={cn(
          "flex items-center gap-3 w-full px-3 py-2.5 rounded-full text-[13px] font-medium transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] text-left group relative active:scale-[0.97]",
          active
            ? "bg-secondary text-secondary-foreground"
            : "text-muted-fg hover:text-text hover:bg-primary/8",
          collapsed && "justify-center px-0 rounded-2xl"
        )}
      >
        <Icon size={18} className={cn("shrink-0", active ? "text-primary" : "text-muted-fg group-hover:text-text")} />
        {!collapsed && <span className="truncate">{label}</span>}
      </button>
    );

    if (collapsed) {
      return (
        <Tooltip key={path} delayDuration={0}>
          <TooltipTrigger asChild>
            <div className="flex flex-col items-center">
              {btn}
              {active && <div className="w-8 h-1 rounded-full bg-primary mt-0.5" />}
            </div>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={8}>{label}</TooltipContent>
        </Tooltip>
      );
    }
    return <div key={path}>{btn}</div>;
  };

  const nav = (
    <aside className={cn(
      "flex flex-col h-full bg-sidebar transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]",
      collapsed ? "w-[72px]" : "w-[260px]"
    )}>
      <div className={cn("flex items-center shrink-0 px-4 py-4", collapsed ? "justify-center" : "gap-2.5")}>
        <CatAvatar size={32} mood="static" className="shrink-0" />
        {!collapsed && (
          <span className="text-lg font-display font-bold text-sidebar-foreground">SparkOffer</span>
        )}
      </div>

      <Separator />

      <TooltipProvider delayDuration={0}>
        <nav className={cn("flex-1 flex flex-col gap-0.5 overflow-y-auto py-3", collapsed ? "px-2" : "px-3")}>
          {NAV_ITEMS.map(navItem)}
        </nav>

        <Separator />

        <div className={cn("py-2 space-y-0.5", collapsed ? "px-2" : "px-3")}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={toggleTheme}
                className={cn(
                  "flex items-center gap-3 w-full py-2.5 px-3 rounded-full text-[13px] font-medium text-muted-fg hover:text-text hover:bg-primary/8 transition-all duration-300 active:scale-[0.97]",
                  collapsed && "justify-center px-0"
                )}
              >
                {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
                {!collapsed && (theme === "dark" ? "浅色模式" : "深色模式")}
              </button>
            </TooltipTrigger>
            {collapsed && <TooltipContent side="right" sideOffset={8}>{theme === "dark" ? "浅色模式" : "深色模式"}</TooltipContent>}
          </Tooltip>

          {user && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={handleLogout}
                  className={cn(
                    "flex items-center gap-3 w-full py-2.5 px-3 rounded-full text-[13px] font-medium text-muted-fg hover:text-red hover:bg-red/8 transition-all duration-300 active:scale-[0.97]",
                    collapsed && "justify-center px-0"
                  )}
                >
                  <LogOut size={18} />
                  {!collapsed && <span className="truncate">{user.name || user.email}</span>}
                </button>
              </TooltipTrigger>
              {collapsed && <TooltipContent side="right" sideOffset={8}>退出登录</TooltipContent>}
            </Tooltip>
          )}

          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => setCollapsed((c: boolean) => !c)}
                className={cn(
                  "flex items-center gap-3 w-full py-2.5 px-3 rounded-full text-[13px] font-medium text-muted-fg hover:text-text hover:bg-primary/8 transition-all duration-300 active:scale-[0.97] mt-1",
                  collapsed && "justify-center px-0"
                )}
              >
                {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
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
          <span className="text-base font-display font-bold text-text">SparkOffer</span>
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
