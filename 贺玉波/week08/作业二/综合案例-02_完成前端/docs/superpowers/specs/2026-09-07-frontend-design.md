# 深度研究助手 · 前端设计文档

日期：2026-09-07
状态：已通过分节确认（4/4 节）

## 1. 背景与目标

后端已完整实现（FastAPI + OpenAI Agents SDK 研究循环，`backend/`）。本设计补齐缺失的**前端**：市场/产品同学输入研究主题后，可发起研究、实时观察研究过程、阅读四类产物（结构化报告 / 来源列表 / 过程记录 / 置信度说明）。

## 2. 范围

**范围内**：
- 首页：主题输入 + 历史研究列表
- 详情页：轮询实时进度 + 四类产物展示 + failed 错误态
- 数据模型 TS 映射、API 客户端封装、轮询 hook
- 单元/行为测试（TDD）、精致视觉（frontend-design 技能）

**范围外**（非目标）：
- 后端任何改动（已实现且通过自检）
- 用户系统、鉴权、分享、导出 PDF
- SSR/SEO（本地内部工具，无此需求）

## 3. 已确认决策

| 决策点 | 结论 |
|---|---|
| 技术栈 | Next.js 15 + React 19 + TypeScript + Tailwind CSS v4 |
| 架构方案 | 方案 A：客户端组件直连 FastAPI（后端 CORS 已放行 3000） |
| 功能范围 | 完整版：首页（提交 + 历史列表）+ 详情页（轮询 + 四类产物） |
| 视觉风格 | 精致视觉，实现阶段用 frontend-design 技能落地 |
| 测试 | Vitest + React Testing Library + MSW，superpowers TDD 红-绿-重构 |
| 参照代码 | 无——已核实综合案例-01 没有 Next.js 前端，09-参考代码/ 无 nextjs-demo，从零搭建 |

## 4. 目录结构

新建 `frontend/`（后端 `backend/` 不动）：

```
frontend/
├── app/
│   ├── layout.tsx              # 全局布局（品牌头、字体、背景）
│   ├── page.tsx                # 首页：主题输入 + 历史研究列表
│   ├── globals.css
│   └── research/[id]/page.tsx  # 详情页：轮询 + 四类产物
├── components/
│   ├── ResearchForm.tsx        # 主题输入框 + 提交
│   ├── ResearchList.tsx        # 历史列表（状态徽标）
│   ├── ProgressPanel.tsx       # 研究过程实时时间线（plan/search/summarize/judge steps）
│   ├── ReportView.tsx          # 结构化报告（摘要/分节/关键结论/遗留问题）
│   ├── ReportHtmlView.tsx      # iframe srcdoc 渲染后端 HTML 报告
│   ├── SourcesView.tsx         # 来源列表
│   ├── ConfidenceView.tsx      # 置信度说明
│   └── StatusBadge.tsx
├── lib/
│   ├── api.ts                  # 4 个接口封装 + NEXT_PUBLIC_API_BASE
│   └── types.ts                # 后端 Pydantic 模型的 TS 映射
└── hooks/
    └── useResearch.ts          # 轮询 hook
```

## 5. 数据模型（types.ts）

后端地址通过 `NEXT_PUBLIC_API_BASE` 环境变量配置（`frontend/.env.local`，默认 `http://127.0.0.1:8000`），`lib/api.ts` 读取。

逐字段映射 `backend/models.py` 的 `ResearchRecord`：

- `Status = 'pending' | 'running' | 'completed' | 'failed'`
- `ResearchRecord`：research_id / topic / status / created_at / updated_at / error / report / report_html / sources / draft / process / confidence
- `ReportContent`：title / summary / sections[] / key_conclusions[] / open_questions[]
- `Section`：heading / body / conclusions[]；`Conclusion`：text / sources[] / is_model_inference
- `Source`：url / title / site_name / snippet / accessed_at
- `ResearchProcess`：plan[] / search_queries[] / reviewed_urls[] / iterations / steps[]
- `ProcessStep`：type（plan|search|summarize|judge）/ round / detail
- `DraftBlock`：round / keyword / text
- `ConfidenceNote`：overall（high|medium|low）/ info_cutoff / notes[]

## 6. 页面与数据流

### 6.1 首页 `/`

1. `ResearchForm`：输入主题 → `POST /api/research` → 202 返回 research_id → `router.push('/research/{id}')`；空输入禁用提交
2. `ResearchList`：`GET /api/research`，created_at 倒序；每条显示主题、状态徽标（pending/running/completed/failed）、创建时间，点击进详情
3. 加载时调 `GET /health`；失败显示「后端未启动」提示条（含重试），不阻塞页面

### 6.2 详情页 `/research/{id}`

`useResearch` hook 轮询：pending/running 每 2 秒 `GET /api/research/{id}`，completed/failed 停止。

- **running**：`ProgressPanel` 实时展示——轮数（iterations）、steps 时间线（中间结果逐步落盘，轮询可见 steps 增量增长）、已累积正文段数（draft.length）、来源数（sources.length）
- **completed**：四类产物分区——结构化报告（ReportView）、HTML 报告（ReportHtmlView，iframe srcdoc，与结构化视图切换）、来源列表（SourcesView）、置信度说明（ConfidenceView）
- **failed**：展示 `error` 字段 + 已保留的中间结果（后端保证异常也落盘）
- 页面注明「关闭页面不影响研究，后端继续执行」

### 6.3 HTML 报告渲染

后端 `report_html` 是自包含 HTML（内嵌 CSS）。用 **iframe srcdoc** 渲染：隔离样式与脚本、不污染页面；结构化报告用后端 report JSON 自行渲染，两视图可切换。

### 6.4 布局修订（2026-09-08，用户确认：双栏布局）

原 768px 单栏在宽屏利用率低。修订为：

- 页面容器放宽至 `max-w-[1440px]`。
- **详情页**：左主区（报告 / HTML 报告页签；running/failed 时实时展示草稿段落 `DraftPreview`）+ 右常驻侧栏（置信度、过程时间线、来源列表，sticky）。来源与置信度不再通过页签切换。
- **首页**：左侧表单卡片（sticky）+ 右侧历史列表分栏。

## 7. 错误处理与边界情况

| 场景 | 处理 |
|---|---|
| 后端未启动 | /health 失败 → 首页提示条（含重试） |
| 空主题提交 | 前端禁用空输入；后端 400 的 detail 也展示 |
| 轮询中网络抖动 | 单次失败不中断轮询，**连续 3 次**失败才提示 |
| rid 不存在（404） | 空态页「研究不存在」+ 返回首页链接 |
| failed 状态 | 展示 error + 已保留的中间结果 |
| 页面关闭再打开 | 研究在后端后台执行，重进页面重新轮询 |
| report_html 为空 | 只显示结构化报告视图，隐藏 HTML 切换 |

## 8. 测试策略（TDD）

Vitest + React Testing Library + MSW 模拟后端。

- **单元**：`api.ts`（请求封装、非 2xx 抛错、错误消息传播）、`useResearch`（completed/failed 停止轮询、卸载清定时器）、`StatusBadge`（四态）、`ProgressPanel`（steps→时间线映射）
- **行为**：提交表单后跳转详情页；轮询中 steps 增量渲染；failed 展示错误与中间结果；404 空态
- **联调**：真实后端 + curl 驱动端到端验证（后端已有 demo 研究数据）

## 9. 视觉设计方向（frontend-design 技能落地）

- 中文排版优先：合适字体族与字号层级（标题/正文/元信息三档以上）
- 克制配色：专业调研工具气质，一个主色 + 中性灰阶；状态徽标四态可区分
- 组件质感：卡片化布局、时间线、徽标、来源条目（站点名 + 日期元信息）
- 亮色主题

## 10. 成功标准

1. `npm run dev` 启动，首页可发起研究、可见历史列表
2. 详情页 running 时实时显示 steps 增长，completed 后四类产物完整可读、来源可点击
3. failed / 后端未启动 / 404 三种异常态按第 7 节呈现
4. 全部测试通过（`npm test`），端到端联调一次真实研究跑通
5. 后端代码零改动

## 11. 已知事实修正

CLAUDE.md 记载「综合案例-01 为 FastAPI + Next.js 前端」与「09-参考代码/ 含 nextjs-demo/」与磁盘实际不符（均不存在）。本设计不依赖参照代码；实现完成后应顺手修正 CLAUDE.md 中的这条记录。
