import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { cn } from "@/lib/utils";

// 按需注册语法高亮：只把面试场景高频的几种语言打进包，而非 Prism 全量入口的 200+ 种。
// 这是构建模块数 / 内存峰值的最大单一来源。未注册的语言会安全降级为纯文本（不报错、仅无配色）。
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import java from "react-syntax-highlighter/dist/esm/languages/prism/java";
import go from "react-syntax-highlighter/dist/esm/languages/prism/go";
import rust from "react-syntax-highlighter/dist/esm/languages/prism/rust";
import cpp from "react-syntax-highlighter/dist/esm/languages/prism/cpp";
import c from "react-syntax-highlighter/dist/esm/languages/prism/c";
import csharp from "react-syntax-highlighter/dist/esm/languages/prism/csharp";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";

// 标准语言名 → 语法定义
const SYNTAX: Record<string, any> = {
  python, javascript, typescript, jsx, tsx, java, go, rust,
  cpp, c, csharp, sql, bash, json, yaml, markdown,
};
// 常见 fenced-code 标记别名 → 标准名（LLM 输出里 ```py / ```sh / ```yml 很常见）
const ALIASES: Record<string, string> = {
  py: "python", js: "javascript", ts: "typescript",
  sh: "bash", shell: "bash", zsh: "bash", console: "bash",
  yml: "yaml", golang: "go", cs: "csharp", md: "markdown",
};
Object.entries(SYNTAX).forEach(([name, syntax]) => SyntaxHighlighter.registerLanguage(name, syntax));
Object.entries(ALIASES).forEach(([alias, target]) => SyntaxHighlighter.registerLanguage(alias, SYNTAX[target]));

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
