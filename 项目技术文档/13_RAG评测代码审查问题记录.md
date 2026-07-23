# RAG 评测代码审查问题记录

审查对象：`0a129ed76ea51f78c8285408363f2a9d088d4355`，即 `feat: add docker-aware RAG evaluation`。

审查范围：RAG 评测后端、RAG Dashboard、Docker/SQLite 持久化、测试与项目分析报告一致性。

审查结论：未发现 P0/P1 阻断级问题；发现 3 个 P2 风险和 2 个 P3 文档/UI 一致性问题。

> 2026-07-23 最终状态：以下 5 项均已修复并纳入回归。`supported` 解析只接受合法语义；Dashboard 历史请求使用 generation fencing；轮询对瞬时错误有限退避；active job 恢复完整评测维度；主报告和交付文档已同步。当前总体验证结果见 `15_全项目代码审查与修复交付总结.md`。

## P2 问题

### 1. 评测支撑度分数可能被字符串 `"false"` 抬高

位置：`backend/rag_eval.py:77`

问题：`_support_weight()` 兼容旧字段 `supported` 时使用 `bool(item.get("supported"))`。如果 LLM 或旧缓存返回 `{"supported": "false"}`，Python 会把非空字符串判为 `True`，从而把实际不支持的 statement/claim 计为满分。

影响：会影响 `context_recall`、`faithfulness`、`answer_correctness` 三类合成端到端评测指标，导致分数偏高。

建议修复：

- 只接受真实 JSON bool：`True -> 1.0`、`False -> 0.0`。
- 如需兼容字符串，应显式解析 `"true"` / `"false"`，其他类型按缺测或 0 处理。
- 补充单元测试覆盖 `supported=False`、`supported="false"`、`support="partial"`、非法字段。

### 2. Dashboard 历史请求存在竞态，可能显示错 Topic 的历史

位置：`frontend/src/pages/RAGDashboard.tsx:225`

问题：`loadEvalHistory()` 没有请求代际或取消控制。初始化时的全量历史请求和切换 Topic 后的过滤请求可能并发，后返回的旧请求会覆盖新状态。

影响：用户选中某个 Topic 时，页面可能显示全量历史；严格可比组统计也会基于错误历史集合计算。

建议修复：

- 给 `loadEvalHistory()` 增加 `historyGenerationRef` 或 `AbortController`。
- 请求返回后校验当前 Topic 与请求发起时一致，只有最后一次请求允许 `setEvalRuns()`。
- 增加前端组件测试或最小状态测试覆盖“慢请求覆盖快请求”。

### 3. 轮询遇到一次临时失败就放弃 active job

位置：`frontend/src/pages/RAGDashboard.tsx:270`

问题：`pollEvalJob()` 的 `catch` 分支会立即 `stopEvalPolling()`、删除 `ACTIVE_RAG_EVAL_KEY` 并设置 `evalRunning=false`。

影响：网络抖动、短暂 502、容器重启窗口都可能让前端丢失仍在运行或稍后可从 SQLite 恢复的评测任务。

建议修复：

- 改为有限重试和指数退避。
- 只有连续失败超过阈值，或明确 404 且历史记录也查不到 terminal run 时，再清理 active key。
- 对 401/403 保持立即停止；对 5xx/网络错误进入 retry。

## P3 问题

### 4. 刷新恢复 active job 时控制区维度可能误导

位置：`frontend/src/pages/RAGDashboard.tsx:324`

问题：localStorage 只保存 `{ job_id, topic }`，没有保存 `eval_kind`、`retrieval_mode`、`judge_mode`、`n_questions`。

影响：刷新恢复后，上方控制区可能显示默认“固定回归 + 生产回放”，但实际正在运行的任务可能是另一个配置。结果卡片会使用后端 status 修正展示，但控制区和说明文案仍可能误导用户。

建议修复：

- active job 写入 localStorage 时保存完整评测维度。
- 或第一次 `status` 返回后，用 `status.eval_kind`、`status.retrieval_mode`、`status.judge_mode`、`status.n_questions` 回填 UI 状态。

### 5. 项目分析报告版本信息过时

位置：

- `项目技术文档/12_全量代码分析报告_Docker部署版.md:3`
- `项目技术文档/12_全量代码分析报告_Docker部署版.md:29`

问题：

- 报告仍写 `commit c2684270fdb5 + 未提交 RAG 评测实现`，但当前实现已经提交并推送为 `0a129ed76ea51f78c8285408363f2a9d088d4355`。
- 报告中的 `codex review` 失败原因与本轮实际复核不一致。本轮通过本地 `7890` 代理执行审查命令，但结果是超时，不是 403。

影响：影响后续按报告追溯代码版本和审查工具状态。

建议修复：

- 把报告基线改为 `commit 0a129ed76ea5` 或完整 SHA。
- 删除“未提交”表述。
- 将 review 结果更新为“通过 7890 代理执行但超时，未产出审查结论”。

## 初审已验证项

- RAG 相关定向测试：`31 passed, 1 warning`
- `npx tsc --noEmit`：通过
- `python -m compileall -q backend`：通过
- Dockerfile、Compose、`rag_eval_runs` schema/migration 未发现明确缺陷

## 最终落实

1. `backend/rag_eval.py` 已严格解析 `supported`，非法字符串不会被当作 `True`。
2. `frontend/src/pages/RAGDashboard.tsx` 已加入历史请求代次、job-specific 恢复和轮询退避。
3. RAG start/job/result/metrics 已加入持久 lease、token fencing、终态 replay 和幂等映射。
4. RAG 相关测试已并入最终后端全量 `343 passed`；前端 lint/typecheck/build 通过。
5. 真实 Qdrant/provider/Docker 网络行为仍按部署验收清单执行，见文档 15。
