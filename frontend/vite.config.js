import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    // 与 tsconfig 的 ES2020 对齐，避免 Vite 默认 target 输出更新语法
    target: "es2020",
    // gzip 体积统计在 syntax-highlighter/three 这类大依赖上又慢又吃内存，仅用于日志，关掉
    reportCompressedSize: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // 把最重的几个库各自拆成独立 chunk：降低单块 minify 的内存峰值，顺带优化按需加载
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (/react-force-graph|force-graph|three|d3-force/.test(id)) return "graph";
          if (/react-syntax-highlighter|refractor|prismjs|highlight\.js|lowlight/.test(id)) return "syntax";
          if (/recharts|d3-|victory-/.test(id)) return "charts";
          if (/react-markdown|remark|rehype|micromark|mdast|hast|unist|unified/.test(id)) return "markdown";
          return "vendor";
        },
      },
    },
  },
})
