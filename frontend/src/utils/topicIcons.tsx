import React from "react";
import {
  FileText, Brain, Bot, Library, Wrench, Plug, Link, Pencil,
  Database, HardDrive, Settings, Code, Container, Terminal,
  Globe, Cpu, Network, Shield, Layers, BookOpen,
  Workflow, Zap, Server, GitBranch, Cloud, Blocks, Hash,
  Binary, Lock, Rocket, FolderCode, MessageSquare,
  type LucideIcon,
} from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  FileText, Brain, Bot, Library, Wrench, Plug, Link, Pencil,
  Database, HardDrive, Settings, Code, Container, Terminal,
  Globe, Cpu, Network, Shield, Layers, BookOpen,
  Workflow, Zap, Server, GitBranch, Cloud, Blocks, Hash,
  Binary, Lock, Rocket, FolderCode, MessageSquare,
};

export const ICON_OPTIONS = Object.entries(ICON_MAP).map(([name, Icon]) => ({
  name,
  Icon,
}));

export function getTopicIcon(iconName: string | undefined | null, size: number = 18): React.ReactElement {
  if (!iconName) {
    const Default = ICON_MAP.FileText;
    return <Default size={size} />;
  }
  const Comp = ICON_MAP[iconName];
  if (Comp) return <Comp size={size} />;
  // Fallback: render as text (backward compat for old emoji data)
  return <span style={{ fontSize: size }}>{iconName}</span>;
}
