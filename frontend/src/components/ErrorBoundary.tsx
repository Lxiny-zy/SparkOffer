import { Component, ReactNode, ErrorInfo } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="flex flex-col items-center justify-center p-10 md:p-15 gap-4 min-h-[60vh]">
        <div className="text-2xl font-bold text-text">出了点问题</div>
        <div className="text-sm text-dim max-w-[400px] text-center break-words">
          {this.state.error?.message || "未知错误"}
        </div>
        <Button
          variant="default"
          className="mt-2"
          onClick={() => this.setState({ error: null })}
        >
          重试
        </Button>
        <Link to="/" className="text-sm text-primary hover:underline">返回首页</Link>
      </div>
    );
  }
}
