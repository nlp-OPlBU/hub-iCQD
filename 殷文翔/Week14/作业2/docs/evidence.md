# 图文分块与来源查看

## 解析与分块

`mineru_artifacts.py` 将实际 MinerU `structured_content.json` 的 `pages/blocks` 转为 `normalized.json` schema 2。Worker 同时保存文档 ID、解析版本及原始 Markdown/JSON/图片；历史产物保持原样。

- 正文：标题、段落等归为 `evidence_type=text`，保留 MinerU 原始 `type` 和 `content`。
- 表格：归为 `table`，保留原始 HTML、说明、脚注及 `table_rows`，将每行单元格用 ` | ` 连接供检索。优先按完整行切块，单行超长时仍按字符上限切分。合并单元格的视觉布局以原始 PDF/表格图片为准。
- 图片：归为 `image`，正文、图片说明和脚注进入检索文本。没有文字说明的图片仍生成独立证据，明确标为“无文字说明”；可借用紧邻的同页正文作为检索上下文，`text_origin=page_context`，不声称这些文字是图片 OCR 或视觉描述。
- `image_source` 与表格 HTML 内的 `img src` 均校验实际文件；禁止绝对路径、目录穿越及逃逸产物目录的引用。图片关联来自所在的页面和块，不根据图片文件名猜测页码。

`chunking.py` 以文档 ID、版本、物理页码、块序号、字符起点生成确定性 `chunk_id`。每块保存：

| 字段 | 含义 |
| --- | --- |
| `document_id` / `version` | MySQL 文档与解析任务身份 |
| `page` | PDF 物理页码，从 1 开始；MinerU `page_idx + 1` |
| `block_index` | 该页 MinerU 块序号，从 0 开始 |
| `char_offset` / `char_end` | 归一化块 `text` 中的 Unicode 码点范围，左闭右开；不是 PDF 文件字节位置 |
| `bbox` | MinerU 页面归一化坐标 `[x0,y0,x1,y1]`，范围 0～1；未提供时为 null |
| `evidence_type` / `text_origin` | 正文、表格、图片及文本来源性质 |
| `image_keys` | 当前原文块直接关联的图片，含表格及单元格图片 |
| `page_image_keys` | 同页图片上下文，供正文证据按预算选用；不等同于直接块关联 |

默认分块上限 1200 字符，不跨页面或原文块拼接。原文位置通过页码与 bbox 回到 PDF；字符范围用于在线查看页的归一化证据高亮。表格检索文本经过结构化转换，不能把这些偏移当作原始 HTML 偏移。

## 检索、回答与来源校验

ES 的版本范围继续由 MySQL 已完成且当前有效的任务决定；问答与 `/api/search` 共用 `sources_for_hits`。新来源的文档版本、页码、块序号、字符范围、bbox、文本及图片键须与对应任务的不可变 `normalized.json` 一致，否则返回 `INVALID_SOURCE_POSITION`。图片还须通过实际页码和文件校验后登记受控 UUID 资源；已登记图片的文档、版本和页码不匹配时拒绝引用。

`SourceReference` 增加 `evidence_type`、`text_origin`、`block_index`、`char_offset`、`char_end`、`bbox` 和 `image_relation`。`image_relation=block` 表示直接块关联，`page` 表示同页图片上下文；没有图片时为 null。来源随回答持久化，不随之后的解析任务更新。表格与图片证据的直接图片优先送入视觉模型，图片数量、字节与 token 预算沿用现有配置。

## 在线查看

- 文档列表的“在线查看”打开原始 PDF 与当前版本解析证据。
- 点击答案 `[S1]` 或来源文档名，新标签页打开 `/documents/:id/view?version=…&page=…&block=…&offset=…&end=…`（前端 hash 路由），保留原会话。
- 左侧使用浏览器 PDF 查看器，`#page=N` 请求定位物理页；右侧显示对应原文块、位置、关联图表，并高亮引用字符范围。可以切换为该页全部证据或输入页码跳转。没有浏览器内置 PDF 能力时可使用“打开原始 PDF”。不在 PDF 画布上绘制 bbox 高亮。
- 解析未完成时仍可查看已上传 PDF；证据区明确显示尚不可用。不存在的版本、页面和文件有明确错误。缺失原文块不会回退到其他块冒充来源。

后端接口：

| 接口 | 行为 |
| --- | --- |
| `GET /api/documents/{id}/content?version=1` | 校验文档和任务版本，按内部存储键返回原始 PDF；inline、nosniff、private 缓存，支持 HTTP Range；可查看尚未解析的上传文档 |
| `GET /api/documents/{id}/pages/{page}?version=1` | 返回 document_id/version/filename/page/page_count，以及该页 blocks 的类型、正文、位置和受控 images；仅已完成解析版本可用 |

两个接口省略 version 时选择当前可检索版本，否则最新任务版本。解析版本共享同一份不可变上传 PDF。接口不接受客户端文件路径，也不返回内部存储键或服务器绝对路径。

## 兼容与上线

沿用现有 MySQL 表结构，无新数据库迁移。ES 新建索引含新增字段；已有配置索引在后续 Worker 索引写入前仅补充三个新增字段的 mapping，不更改已有 analyzer/字段类型。实际执行仍需项目账号具备该索引 mapping 更新权限；映射冲突返回索引失败，不静默忽略。

旧 schema 1 产物与已保存会话可继续查看。旧索引缺少的来源类型/字符终点不伪造；来源仍可定位文档和已有页码。旧数据不会自动获得图片独立分块、表格行文本和内嵌图片关联；要使用新规则，请重新上传解析。当前人工重试接口仅允许失败任务，不能对 completed 任务直接调用重试。不要原地覆盖已完成任务产物或已保存会话来源。

本轮未重启部署进程，也未修改已有业务数据、共享服务或真实索引；在线部署更新、实际 PDF 查看器页码行为以及真实 MySQL/ES/模型完整链路仍需部署后验证。本轮检查见 [验证记录](verification.md)。
