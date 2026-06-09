import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col items-center justify-center p-10 md:p-15 gap-4 min-h-[60vh]">
      <div className="sig-display text-7xl text-[color:var(--sig-faint)] opacity-50 animate-bounce-in">404</div>
      <div className="text-xl font-semibold text-text">页面不存在</div>
      <div className="text-sm text-dim">你访问的页面可能已移除或地址有误</div>
      <Button variant="default" className="mt-3" onClick={() => navigate("/")}>
        返回首页
      </Button>
    </div>
  );
}
