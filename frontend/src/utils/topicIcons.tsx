import React from "react";
import {
  FileText, Brain, Bot, Library, Wrench, Plug, Link, Pencil,
  Database, HardDrive, Settings, Code, Container, Terminal,
  Globe, Cpu, Network, Shield, Layers, BookOpen,
  Workflow, Zap, Server, GitBranch, Cloud, Blocks, Hash,
  Binary, Lock, Rocket, FolderCode, MessageSquare,
  CircleHelp,
  type LucideIcon,
} from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  FileText, Brain, Bot, Library, Wrench, Plug, Link, Pencil,
  Database, HardDrive, Settings, Code, Container, Terminal,
  Globe, Cpu, Network, Shield, Layers, BookOpen,
  Workflow, Zap, Server, GitBranch, Cloud, Blocks, Hash,
  Binary, Lock, Rocket, FolderCode, MessageSquare, CircleHelp,
};

const LEGACY_ICON_ALIASES: Record<string, keyof typeof ICON_MAP> = {
  "\u{1F4DD}": "FileText",
  "\u{1F9E0}": "Brain",
  "\u{1F916}": "Bot",
  "\u{1F4DA}": "Library",
  "\u{1F527}": "Wrench",
  "\u{1F50C}": "Plug",
  "\u{1F517}": "Link",
  "\u270F": "Pencil",
  "\u270F\uFE0F": "Pencil",
  "\u{1F5C4}": "Database",
  "\u{1F5C4}\uFE0F": "Database",
  "\u{1F4BE}": "HardDrive",
  "\u2699": "Settings",
  "\u2699\uFE0F": "Settings",
  "\u{1F4BB}": "Code",
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
  const normalizedName = LEGACY_ICON_ALIASES[iconName] || iconName;
  const Comp = ICON_MAP[normalizedName];
  if (Comp) return <Comp size={size} />;
  // Unknown/legacy values still resolve to a Lucide glyph: the interface never
  // falls back to raw emoji or arbitrary text masquerading as an icon.
  const Fallback = ICON_MAP.CircleHelp;
  return <Fallback size={size} />;
}
