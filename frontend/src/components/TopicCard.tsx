import { getTopicIcon } from "../utils/topicIcons";
import { cn } from "@/lib/utils";

interface TopicCardProps {
  topicKey: string;
  name: string;
  icon?: string;
  onClick?: () => void;
  selected?: boolean;
}

export default function TopicCard({ topicKey, name, icon, onClick, selected }: TopicCardProps) {
  return (
    <div
      data-spotlight={selected ? undefined : ""}
      style={selected ? { borderColor: "var(--sig-accent)", background: "color-mix(in srgb, var(--sig-accent) 8%, transparent)" } : undefined}
      className={cn(
        "sig-card flex items-center gap-3 px-5 py-4 cursor-pointer text-left active:scale-[0.97] relative overflow-hidden",
        selected ? "" : "sig-hover-lift spotlight"
      )}
      onClick={onClick}
    >
      <div className={cn("w-10 h-10 flex items-center justify-center rounded-md transition-transform duration-300", selected && "scale-110")} style={{ background: "color-mix(in srgb, var(--sig-accent) 12%, transparent)", color: "var(--sig-accent)" }}>
        {getTopicIcon(icon, 22)}
      </div>
      <div>
        <div className="text-[15px] font-medium text-text">{name}</div>
        <div className="text-xs text-dim mt-0.5">{topicKey}</div>
      </div>
    </div>
  );
}
