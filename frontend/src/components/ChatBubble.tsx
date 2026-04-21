import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";

interface ChatBubbleProps {
  role: "user" | "assistant";
  content: string;
}

export default function ChatBubble({ role, content }: ChatBubbleProps) {
  if (role === "user") {
    return (
      <div className="flex justify-end animate-fade-in">
        <div className="max-w-[70%] px-4 py-2.5 rounded-3xl rounded-tr-lg bg-primary text-primary-foreground text-[15px] leading-[1.7] whitespace-pre-wrap shadow-sm">
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col animate-fade-in">
      <div className="h-px bg-border mb-6" />
      <div className="max-w-full md:max-w-[720px] leading-[1.8] text-[15px] text-text">
        <div className="md-content">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
