import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { cn } from "@/lib/utils";

interface ChatBubbleProps {
  role: "user" | "assistant";
  content: string;
}

/** 从 className 里解析语言名，react-markdown 会注入 language-xxx */
function getLang(className?: string): string {
  if (!className) return "";
  const match = className.match(/language-(\w+)/);
  return match ? match[1] : "";
}

/** 统一传给 ReactMarkdown 的 remark 插件列表，启用 GFM（表格/删除线/任务列表等） */
export const remarkPlugins: React.ComponentProps<typeof ReactMarkdown>["remarkPlugins"] = [remarkGfm];

/** 统一的 ReactMarkdown components，供各处复用 */
export const markdownComponents: React.ComponentProps<typeof ReactMarkdown>["components"] = {
  table({ children, ...props }) {
    return (
      <div className="md-table-wrapper">
        <table {...props}>{children}</table>
      </div>
    );
  },
  // 代码块：在 pre 组件中直接用 SyntaxHighlighter 渲染
  pre({ children }) {
    // 从 children 中提取 code 组件的 props
    const codeElement = children as React.ReactElement<{ className?: string; children?: React.ReactNode }>;
    const className = codeElement?.props?.className || "";
    const code = codeElement?.props?.children;
    const lang = getLang(className);
    const codeString = String(code).replace(/\n$/, "");

    return (
      <div className="md-code-wrapper">
        {lang && <span className="md-code-lang">{lang}</span>}
        <SyntaxHighlighter
          language={lang.toLowerCase() || "text"}
          style={oneDark}
          customStyle={{
            margin: 0,
            borderRadius: "12px",
            fontSize: "13px",
            padding: "16px 18px",
          }}
          showLineNumbers={false}
        >
          {codeString}
        </SyntaxHighlighter>
      </div>
    );
  },
  // 行内代码保持原样
  code({ className, children, ...props }) {
    const lang = getLang(className);
    const isBlock = Boolean(lang || className?.includes("language-"));
    if (isBlock) return null; // 代码块由 pre 组件处理
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
};

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
          <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
