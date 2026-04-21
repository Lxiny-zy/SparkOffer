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
      className={cn(
        "flex items-center gap-3 px-5 py-4 rounded-3xl cursor-pointer transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] text-left active:scale-[0.97]",
        selected
          ? "bg-secondary shadow-md ring-2 ring-primary"
          : "bg-card shadow-sm hover:shadow-md hover:scale-[1.02]"
      )}
      onClick={onClick}
    >
      <div className="w-10 h-10 flex items-center justify-center rounded-2xl bg-primary/12 text-primary">
        {getTopicIcon(icon, 22)}
      </div>
      <div>
        <div className="text-[15px] font-medium text-text">{name}</div>
        <div className="text-xs text-dim mt-0.5">{topicKey}</div>
      </div>
    </div>
  );
}
