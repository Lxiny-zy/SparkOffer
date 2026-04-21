import { useState, useEffect, useRef, useCallback } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { getTopics, getGraphData } from "../api/interview";
import { getTopicIcon } from "../utils/topicIcons";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { TopicInfo } from "../types/api";

const SIMILARITY_THRESHOLD = 0.65;

function scoreToColor(score: number): string {
  if (score >= 8) return "#22C55E";
  if (score >= 6) return "#FBBF24";
  if (score >= 4) return "#FB923C";
  return "#EF4444";
}

export default function Graph() {
  const [topics, setTopics] = useState<Record<string, TopicInfo>>({});
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);
  const [graphData, setGraphData] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [hoveredNode, setHoveredNode] = useState<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number }>({ width: 800, height: 500 });

  useEffect(() => {
    getTopics().then(setTopics).catch(() => {});
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries: ResizeObserverEntry[]) => {
      const { width } = entries[0].contentRect;
      setDimensions({ width, height: Math.max(280, Math.min(width * 0.65, 600)) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const handleSelectTopic = async (key: string) => {
    setSelectedTopic(key);
    setGraphData(null);
    setLoading(true);
    try {
      const data = await getGraphData(key);
      setGraphData(data);
      setTimeout(() => fgRef.current?.zoomToFit(400, 40), 300);
    } catch {
      setGraphData({ nodes: [], links: [] });
    } finally {
      setLoading(false);
    }
  };

  const paintNode = useCallback((node: any, ctx: CanvasRenderingContext2D) => {
    const r = 5 + (node.difficulty || 3) * 1.2;
    const color = scoreToColor(node.score);
    const isLight = !document.documentElement.classList.contains("dark");
    const textColor = isLight ? "#18181B" : "#FAFAF9";

    if (hoveredNode === node) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 16;
    }

    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.shadowBlur = 0;

    if (hoveredNode === node) {
      ctx.strokeStyle = textColor;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    const label = node.focus_area || node.question.slice(0, 20);
    ctx.font = `${hoveredNode === node ? 12 : 10}px DM Sans, sans-serif`;
    ctx.textAlign = "center";
    ctx.fillStyle = textColor;
    ctx.globalAlpha = hoveredNode === node ? 1 : 0.7;
    ctx.fillText(label, node.x, node.y - r - 5);
    ctx.globalAlpha = 1;
  }, [hoveredNode]);

  const paintLink = useCallback((link: any, ctx: CanvasRenderingContext2D) => {
    const alpha = Math.max(0.08, (link.similarity - SIMILARITY_THRESHOLD) * 3);
    ctx.strokeStyle = `rgba(161, 161, 170, ${alpha})`;
    ctx.lineWidth = 0.5 + link.similarity * 1.5;
    ctx.beginPath();
    ctx.moveTo(link.source.x, link.source.y);
    ctx.lineTo(link.target.x, link.target.y);
    ctx.stroke();
  }, []);

  const topicEntries = Object.entries(topics);

  return (
    <div className="flex-1 px-4 py-8 md:px-6 md:py-10 max-w-4xl mx-auto w-full">
      <h1 className="text-2xl md:text-[28px] font-display font-bold mb-6 animate-fade-in">题目图谱</h1>

      <div className="flex flex-wrap gap-2 mb-6 animate-fade-in-up">
        {topicEntries.map(([key, info]) => (
          <Button
            key={key}
            variant={selectedTopic === key ? "secondary" : "ghost"}
            size="sm"
            className={selectedTopic === key ? "border-primary border" : ""}
            onClick={() => handleSelectTopic(key)}
          >
            <span className="inline-flex align-middle mr-1">{getTopicIcon(info.icon, 14)}</span>{info.name}
          </Button>
        ))}
      </div>

      <Card ref={containerRef} className="relative animate-fade-in-up [animation-delay:0.1s]" style={{ minHeight: 280 }}>
        <CardContent className="p-0">
          {!selectedTopic && (
            <div className="flex items-center justify-center h-[280px] text-dim text-sm">
              选择一个领域查看题目关联图谱
            </div>
          )}

          {loading && (
            <div className="flex items-center justify-center h-[280px] text-dim text-sm gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot" />
              <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-dot [animation-delay:0.2s]" />
              正在构建图谱...
            </div>
          )}

          {selectedTopic && !loading && graphData && graphData.nodes.length === 0 && (
            <div className="flex items-center justify-center h-[280px] text-dim text-sm">
              该领域暂无已评分的训练记录
            </div>
          )}

          {selectedTopic && !loading && graphData && graphData.nodes.length > 0 && (
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              width={dimensions.width}
              height={dimensions.height}
              backgroundColor="transparent"
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
                const r = 5 + (node.difficulty || 3) * 1.2;
                ctx.beginPath();
                ctx.arc(node.x, node.y, r + 4, 0, 2 * Math.PI);
                ctx.fillStyle = color;
                ctx.fill();
              }}
              linkCanvasObject={paintLink}
              onNodeHover={setHoveredNode}
              cooldownTicks={80}
              d3AlphaDecay={0.03}
              d3VelocityDecay={0.3}
            />
          )}

          {hoveredNode && (
            <div className="absolute top-3 right-3 bg-secondary border border-border rounded-lg px-4 py-3 max-w-[280px] text-sm pointer-events-none animate-fade-in z-10 shadow-lg">
              <div className="font-medium text-text leading-snug mb-2">{hoveredNode.question}</div>
              <div className="flex items-center gap-3 text-[13px] text-dim">
                <span style={{ color: scoreToColor(hoveredNode.score) }}>{hoveredNode.score}/10</span>
                {hoveredNode.focus_area && <span>{hoveredNode.focus_area}</span>}
                {hoveredNode.date && <span>{hoveredNode.date}</span>}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {selectedTopic && graphData && graphData.nodes.length > 0 && (
        <div className="flex items-center gap-5 mt-4 text-[13px] text-dim animate-fade-in">
          {[
            { color: "bg-green", label: "8+" },
            { color: "bg-primary", label: "6-8" },
            { color: "bg-orange", label: "4-6" },
            { color: "bg-red", label: "<4" },
          ].map((item) => (
            <div key={item.label} className="flex items-center gap-1.5">
              <span className={cn("w-2.5 h-2.5 rounded-full inline-block", item.color)} />
              <span>{item.label}</span>
            </div>
          ))}
          <span className="ml-auto">共 {graphData.nodes.length} 题</span>
        </div>
      )}
    </div>
  );
}
