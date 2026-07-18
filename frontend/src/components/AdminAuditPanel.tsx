import { useState, useEffect, useCallback } from "react";
import { ShieldCheck, Users, RefreshCw, Loader2, Crown } from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { getAuditLogs, getAdminUsers } from "@/api/settings";

interface AuditItem {
  id: number;
  event: string;
  user_id?: string;
  email?: string;
  ip?: string;
  detail?: string;
  created_at: string;
}

interface AdminUser {
  id: string;
  email: string;
  name: string;
  created_at: string;
  is_owner: boolean;
}

const EVENT_LABEL: Record<string, string> = {
  login_success: "登录成功",
  login_failed: "登录失败",
  login_rate_limited: "登录限流",
  register_success: "注册成功",
  register_blocked: "注册拦截",
  register_rate_limited: "注册限流",
  password_changed: "修改密码",
  password_change_failed: "改密失败",
  profile_updated: "更新资料",
  ai_config_updated: "AI 配置变更",
  channels_updated: "渠道变更",
  tuning_updated: "调参变更",
};

const RISK_EVENTS = new Set([
  "login_failed", "login_rate_limited", "register_blocked",
  "register_rate_limited", "password_change_failed",
]);

const PAGE_SIZE = 50;

export default function AdminAuditPanel() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [items, setItems] = useState<AuditItem[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [eventFilter, setEventFilter] = useState("");
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (filter: string, offset: number) => {
    setLoading(true);
    try {
      const data = await getAuditLogs({ event: filter || undefined, limit: PAGE_SIZE, offset });
      setTotal(data.total);
      setEvents(data.events || []);
      setItems((prev) => (offset === 0 ? data.items : [...prev, ...data.items]));
    } catch { /* 403 etc — silent */ } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(eventFilter, 0);
    getAdminUsers().then((d) => setUsers(d.users || [])).catch(() => {});
  }, [eventFilter, load]);

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-tertiary/10 flex items-center justify-center">
              <Users className="w-5 h-5 text-tertiary" />
            </div>
            <div>
              <CardTitle className="text-base">用户列表</CardTitle>
              <CardDescription>已注册的全部账号（{users.length}）</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {users.map((u) => (
              <div key={u.id} className="flex items-center gap-3 text-sm py-1.5 border-b border-border/40 last:border-0">
                <span className="sig-mono text-[11px] text-dim w-[72px] shrink-0">{u.id}</span>
                <span className="truncate flex-1">{u.email}</span>
                <span className="text-dim truncate max-w-[120px]">{u.name}</span>
                {u.is_owner && (
                  <span className="flex items-center gap-1 text-[11px] font-medium px-1.5 py-0.5 rounded"
                        style={{ background: "color-mix(in srgb, var(--sig-warning) 15%, transparent)", color: "var(--sig-warning)" }}>
                    <Crown size={11} /> 管理员
                  </span>
                )}
                <span className="sig-mono text-[11px] text-dim shrink-0">{u.created_at?.slice(0, 10)}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center">
                <ShieldCheck className="w-5 h-5 text-primary" />
              </div>
              <div>
                <CardTitle className="text-base">安全审计日志</CardTitle>
                <CardDescription>登录 / 注册 / 配置变更事件（共 {total} 条）</CardDescription>
              </div>
            </div>
            <Button size="sm" variant="outline" className="gap-1.5" onClick={() => load(eventFilter, 0)} disabled={loading}>
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              刷新
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-1.5 mb-4">
            <button
              onClick={() => setEventFilter("")}
              className={`text-[11px] sig-mono px-2 py-1 rounded border transition-colors ${!eventFilter ? "border-primary text-primary bg-primary/10" : "border-border text-dim hover:text-text"}`}
            >
              全部
            </button>
            {events.map((ev) => (
              <button
                key={ev}
                onClick={() => setEventFilter(ev)}
                className={`text-[11px] sig-mono px-2 py-1 rounded border transition-colors ${eventFilter === ev ? "border-primary text-primary bg-primary/10" : "border-border text-dim hover:text-text"}`}
              >
                {EVENT_LABEL[ev] || ev}
              </button>
            ))}
          </div>

          <div className="space-y-1">
            {items.map((it) => (
              <div key={it.id} className="flex items-center gap-3 text-[12.5px] py-1.5 border-b border-border/40 last:border-0">
                <span className="sig-mono text-[11px] text-dim w-[130px] shrink-0">{it.created_at}</span>
                <span
                  className="text-[11px] font-medium px-1.5 py-0.5 rounded shrink-0"
                  style={RISK_EVENTS.has(it.event)
                    ? { background: "color-mix(in srgb, var(--sig-danger) 12%, transparent)", color: "var(--sig-danger)" }
                    : { background: "color-mix(in srgb, var(--sig-success) 12%, transparent)", color: "var(--sig-success)" }}
                >
                  {EVENT_LABEL[it.event] || it.event}
                </span>
                <span className="truncate flex-1 text-dim">{it.email || it.user_id || "—"}</span>
                <span className="sig-mono text-[11px] text-dim shrink-0">{it.ip || ""}</span>
              </div>
            ))}
            {items.length === 0 && !loading && (
              <div className="text-sm text-dim py-4 text-center">暂无记录</div>
            )}
          </div>

          {items.length < total && (
            <div className="flex justify-center mt-4">
              <Button size="sm" variant="outline" onClick={() => load(eventFilter, items.length)} disabled={loading}>
                加载更多（{items.length}/{total}）
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
