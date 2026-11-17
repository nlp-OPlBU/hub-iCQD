# 多模态 RAG 系统 Elasticsearch 索引设计说明书

**文档版本：** V1.0  
**设计范围：** Elasticsearch 索引结构、中文分词、向量检索、RRF 融合、Chunk 管理、图片关联、版本管理、Bulk 写入、检索异常和一致性检查  
**关联文档：** 需求规格说明书 V1.0、总体技术架构与技术选型 V1.1、核心数据结构设计 V1.0、Kafka 消息与 Worker 设计 V1.0

---

## 1. 设计目标

Elasticsearch 是多模态 RAG 系统的核心检索引擎，负责：

1. BM25 关键词检索。
2. Dense Vector 向量检索。
3. 使用 RRF 融合多路检索结果。
4. 保存 Chunk 文本、页码、标题、图片关联和 Embedding。

SQLite 负责文档、解析版本和业务状态管理；本地文件系统保存 PDF、解析产物和图片。

Elasticsearch 不负责维护文档的最终业务状态。

核心原则：

**SQLite 是业务状态权威来源，Elasticsearch 是可以重建的检索数据存储。**

## 2. 技术选型

| 项目 | 选型 |
|---|---|
| Elasticsearch | 8.19.25 |
| 索引组织 | 单一共享 Chunk 索引 |
| 中文分词 | Python jieba |
| ES Analyzer | whitespace |
| 关键词检索 | BM25 |
| 向量检索 | HNSW |
| 向量索引类型 | int8_hnsw |
| Embedding 模型 | BAAI/bge-small-zh-v1.5 |
| 向量维度 | 512 |
| 融合算法 | 原生 RRF Retriever |
| Bulk 写入 | AsyncElasticsearch + async_streaming_bulk |
| 索引管理 | Alias |
| 数据一致性 | 自动检测 + CLI 手动修复 |

## 3. 索引组织方式

采用单一共享 Chunk 索引。

### 3.1 索引命名

| 名称 | 用途 |
|---|---|
| rag_chunks | 应用访问 Alias |
| rag_chunks_v1 | 第一版物理索引 |
| rag_chunks_v2 | 后续结构升级使用 |

`v1` 表示 Mapping 结构版本，不表示 PDF 解析版本。

### 3.2 Chunk 文档标识

每条 ES 文档对应一个 Chunk。

通过以下字段区分来源：

- document_id
- document_version_id
- chunk_index

ES `_id` 使用确定性 ID：

```text
{document_version_id}_chunk_{chunk_index:06d}
```

这样可以保证：

- 同一个 Chunk 重复写入不会产生重复记录。
- Worker 崩溃后可以幂等补写。
- 不同解析版本互不覆盖。
- 可以按 document_id 或 document_version_id 删除数据。

## 4. 中文分词设计

### 4.1 分词方案

第一版使用 Python jieba，不安装 Elasticsearch IK 或 SmartCN 插件。

Worker 写入 ES 前执行 jieba 分词；FastAPI 查询 BM25 前执行相同的分词逻辑。

### 4.2 字段设计

| 字段 | 内容 | 用途 |
|---|---|---|
| content | 原始 Chunk 文本 | 展示、上下文构建 |
| content_terms | 正文分词结果 | BM25 |
| heading | 原始标题 | 展示 |
| heading_terms | 标题分词结果 | BM25 |

Elasticsearch 使用 whitespace Analyzer，不重复进行中文分词。

### 4.3 分词一致性

索引阶段和查询阶段必须使用相同的：

- jieba 版本。
- 自定义词典。
- 文本规范化规则。
- 大小写处理规则。
- 分词结果过滤规则。

建议封装统一模块：

```text
app/services/text_processing/
├── tokenizer.py
└── custom_dict.txt
```

示例：

```python
import jieba

jieba.load_userdict("custom_dict.txt")

def tokenize(text: str) -> str:
    if not text:
        return ""

    tokens = jieba.lcut(text)

    tokens = [
        token.strip().lower()
        for token in tokens
        if token.strip()
    ]

    return " ".join(tokens)
```

实际实现时需要补充文本规范化、词典加载初始化和版本固定。

如果将来更换 jieba 版本或词典，应评估是否需要重新生成分词字段和索引数据。

## 5. Chunk 切分策略

### 5.1 初始参数

| 参数 | 值 |
|---|---:|
| 目标长度 | 400 tokens |
| 最大长度 | 450 tokens |
| 重叠长度 | 60 tokens |
| 最小长度 | 50 tokens |

以上均为第一版初始参数，后续根据真实 PDF 检索效果调整。

### 5.2 切分顺序

采用结构优先策略：

1. 标题。
2. 段落。
3. 句子。

尽量保留 PDF 原始内容结构。

### 5.3 特殊内容

表格：

- 尽量保持完整。
- 表格过长时按行拆分。
- 拆分后的 Chunk 重复表头。

图片：

- 不改变文本 Chunk 的切分策略。
- 图片通过关联字段连接 Chunk。

Token 长度使用与 Embedding 模型兼容的 tokenizer 估算，不能只按字符数量计算。

## 6. Chunk 元数据 Mapping

每条 Chunk 文档包含以下字段：

| 字段 | ES 类型 | 说明 |
|---|---|---|
| document_id | keyword | 文档 ID |
| document_version_id | keyword | 解析版本 ID |
| chunk_index | integer | Chunk 序号 |
| content | text | 原始正文，index=false |
| content_terms | text | 正文分词 |
| heading | text | 原始标题，index=false |
| heading_terms | text | 标题分词 |
| heading_path | keyword | 标题路径 |
| page_start | integer | 起始页码 |
| page_end | integer | 结束页码 |
| content_type | keyword | text/table/mixed |
| image_asset_ids | keyword | 图片资产 ID 列表 |
| embedding | dense_vector | 512 维向量 |

原始 PDF 路径、图片文件路径、MinerU 原始 JSON、上传时间、解析任务状态和当前有效版本等业务信息不存入 ES。

## 7. PDF 图片关联设计

采用混合关联方式：

1. 优先依据 MinerU 解析结构建立图片与文本 Chunk 的精确关联。
2. 无法可靠判断时，以页码关联作为兜底。
3. 一个 Chunk 可以关联多张图片。
4. 一张图片可以关联多个 Chunk。

ES 中只保存：

`image_asset_ids`

图片元数据保存在 SQLite 的 `document_assets` 表，图片文件保存在本地文件系统。

检索命中 Chunk 后：

1. 从 ES 获取 image_asset_ids。
2. 查询 SQLite document_assets。
3. 获取图片元数据和文件路径。
4. 由 Context Builder 加载对应图片。

第一版不对 PDF 提取图片进行独立向量化检索。

## 8. Embedding 和向量检索

### 8.1 Embedding 模型

| 参数 | 值 |
|---|---|
| 模型 | BAAI/bge-small-zh-v1.5 |
| 维度 | 512 |
| 输出类型 | float |
| 相似度 | cosine |

### 8.2 HNSW 配置

采用 HNSW 近似最近邻检索。

| 参数 | 值 |
|---|---:|
| 类型 | int8_hnsw |
| m | 16 |
| ef_construction | 100 |
| k | 20 |
| num_candidates | 100 |

`m` 和 `ef_construction` 属于索引构建参数；`k` 和 `num_candidates` 属于查询参数。

初始采用默认构建参数，并在实际检索测试后决定是否调整。

### 8.3 向量归一化

Python 端统一执行 L2 归一化。

文档 Chunk 和用户查询使用相同的向量处理规范。

Elasticsearch 的 cosine 相似度本身也会执行相应归一化，因此 Python 归一化主要用于统一模型输出处理规范。

### 8.4 向量检索示例

```python
response = es.search(
    index="rag_chunks",
    knn={
        "field": "embedding",
        "query_vector": query_embedding,
        "k": 20,
        "num_candidates": 100,
        "filter": {
            "terms": {
                "document_version_id": current_version_ids
            }
        }
    },
    size=20,
    source=[
        "document_id",
        "document_version_id",
        "chunk_index",
        "content",
        "heading",
        "page_start",
        "page_end",
        "image_asset_ids"
    ]
)
```

以上为查询逻辑示例，实际参数格式需与项目采用的 Elasticsearch Python 客户端版本核对。

### 8.5 版本过滤

FastAPI 从 SQLite 查询当前有效的 document_version_id。

BM25 和 HNSW 都必须使用当前有效版本过滤。

HNSW 使用 `knn.filter` 进行预过滤，避免旧版本 Chunk 占用候选名额。

不在 ES 中维护 `is_current` 字段，避免 SQLite 和 ES 两边出现当前版本状态不一致。

## 9. BM25 检索设计

### 9.1 字段权重

| 字段 | 权重 |
|---|---:|
| heading_terms | 2 |
| content_terms | 1 |

标题匹配对最终相关性贡献更高。

### 9.2 查询流程

1. 获取用户原始问题。
2. 根据历史上下文执行 Query Rewrite。
3. 对检索查询执行 jieba 分词。
4. 使用当前有效版本过滤条件。
5. 执行 BM25 检索。
6. 返回 Top 20 候选。

Query Rewrite 只服务于检索，最终回答仍结合原始问题和历史上下文。

## 10. RRF 融合设计

采用 Elasticsearch 原生 RRF Retriever。

### 10.1 参数

| 参数 | 值 |
|---|---:|
| BM25 候选 | 20 |
| HNSW 候选 | 20 |
| rank_window_size | 20 |
| rank_constant | 60 |
| 融合结果 | 20 |

### 10.2 执行流程

```text
                 用户问题
                    |
             +------+------+
             |             |
             v             v
          BM25           HNSW
         Top 20          Top 20
             |             |
             +------+------+
                    |
                    v
                 RRF 融合
                    |
                    v
               ES Top 20
                    |
                    v
              Python 多样性处理
                    |
                    v
                最终 Top 5
```

RRF 根据不同检索结果的排名进行融合，不代表语义相似度分数。

不能直接把 RRF 分数当作语义相似度阈值。

## 11. 多样性处理策略

由 Python 应用层处理，不依赖 Elasticsearch collapse。

规则：

1. 去除重复或高度重复 Chunk。
2. 相邻 Chunk 根据内容互补性决定是否保留。
3. 第一轮每份 PDF 最多选择 2 个 Chunk。
4. 候选不足 5 个时放宽限制。
5. 按 document_id 计算文档分布，而不是按 document_version_id。
6. 如果只有一份 PDF 存在相关内容，不为了文档多样性丢弃有效结果。

最终提供给 Context Builder 的数量为 Top 5。

## 12. Elasticsearch 分片与副本

第一版配置：

| 参数 | 值 |
|---|---:|
| number_of_shards | 1 |
| number_of_replicas | 0 |

当前部署为单节点 Elasticsearch 8.19.25。

采用单主分片、零副本，减少资源开销。

需要重点关注服务器总内存、JVM Heap、GC、Bulk 写入失败和搜索延迟。

当前 JVM Heap 为 512 MB，向量检索与 Bulk 工作负载可能带来内存压力，应在真实数据量下监控。

一个主分片不代表只允许一个 Worker 执行写入，也不与 Worker 并发数一一对应。

## 13. Bulk 写入策略

采用：

- AsyncElasticsearch。
- async_streaming_bulk。

初始参数：

| 参数 | 值 |
|---|---:|
| 每批最大 Chunk 数 | 100 |
| 每批最大请求体 | 5 MB |
| 写入操作 | index |
| ID | 确定性 Chunk ID |

### 13.1 写入流程

```text
获取待写入 Chunk
      |
      v
检查 Worker 租约
      |
      v
检查 Document 状态
      |
      v
Streaming Bulk
      |
      v
逐条检查写入结果
      |
      v
失败项目有限重试
      |
      v
全部写入完成
      |
      v
Refresh
      |
      v
按 document_version_id 核对数量
      |
      v
进入 finalizing
```

### 13.2 幂等写入

使用确定性 `_id` 和 `index` 操作。

相同 Chunk 重复写入时替换同 ID 文档，不创建重复记录。

Bulk 写入必须逐条检查结果，只重试失败项目。

Bulk 操作建议使用 `require_alias=true`，避免意外直接写入物理索引。

### 13.3 租约检查

每批写入前后检查：

- Worker lease_token。
- Worker 当前任务状态。
- Document 是否处于 deleting。

失去租约时立即停止后续写入。

Async Streaming Bulk 内部重试期间不容易插入租约检查，因此实现时必须限制重试次数和持续时间，必要时使用手动分批处理。

## 14. 解析版本管理

采用新旧版本共存、成功后切换。

### 14.1 新版本解析

```text
旧版本继续有效
       |
       v
创建新解析版本
       |
       v
新版本 Chunk 写入 ES
       |
       v
Refresh
       |
       v
核对 Chunk 数量
       |
       v
SQLite 事务切换 current_version_id
       |
       v
新版本生效
       |
       v
清理旧版本 ES 和本地文件
```

### 14.2 失败处理

新版本解析失败：

- 旧版本继续有效。
- current_version_id 不变。
- 新版本标记失败。
- 新版本临时数据按恢复规则清理。

### 14.3 版本隔离

每个解析版本使用独立 document_version_id。

检索始终通过 SQLite 的 current_version_id 过滤。

ES 中存在 Chunk 不代表该版本有效。

只有 SQLite 指向的版本才能参与正式检索。

## 15. 文档删除策略

采用组合删除策略。

删除整份 PDF：

- 根据 document_id 清理全部解析版本 Chunk。

清理单个解析版本：

- 根据 document_version_id 清理。

### 15.1 删除流程

1. SQLite 事务标记 documents.status=deleting。
2. 取消关联任务。
3. Worker 停止后续执行和写入。
4. 等待 Worker 退出或确认租约失效。
5. 对所有需要管理的物理索引清理对应 Chunk。
6. 清理本地 PDF 和解析产物。
7. 核对 ES 和文件系统。
8. 最终删除 SQLite 文档记录。

### 15.2 删除异常恢复

SQLite 和 Elasticsearch 不存在跨系统事务。

因此：

- 删除操作必须幂等。
- 删除失败保留 deleting 状态。
- 后台恢复流程可以再次清理。
- ES 删除后需要考虑 Refresh 带来的搜索可见性延迟。
- 不能只通过 Alias 删除，因为旧物理索引可能仍存在数据。

Worker 租约 fencing token 不能撤销已经发出的 ES 请求，因此删除过程需要重复清理并最终核对。

## 16. 检索异常处理

采用原生 RRF 单次 ES 检索请求。

初始参数：

| 参数 | 值 |
|---|---:|
| ES Search API timeout | 2 秒 |
| Python request_timeout | 5 秒 |
| 额外重试次数 | 1 次 |
| 重试间隔 | 约 0.5 秒 |

异常规则：

- 整体请求失败或超时：有限重试一次。
- 重试仍失败：终止本次 RAG 问答。
- 不绕过检索直接调用 LLM。
- 正常返回空结果不视为异常。
- 超时或分片失败不将部分结果当作完整结果。

## 17. 索引生命周期与升级策略

第一版不启用 ILM，不按时间自动滚动或删除索引。

应用统一使用 Alias：

`rag_chunks`

物理索引：

- rag_chunks_v1
- rag_chunks_v2
- 后续按版本递增。

### 17.1 升级流程

```text
创建新物理索引
       |
       v
暂停 Worker ES 写入
       |
       v
迁移或重新生成数据
       |
       v
验证数据完整性
       |
       v
原子切换 Alias
       |
       v
恢复写入
       |
       v
保留旧索引观察
       |
       v
确认无误后清理旧索引
```

第一版允许维护窗口暂停 ES 写入，不引入双写机制。

### 17.2 需要重新生成数据的变化

以下变化不能简单依靠 Reindex 复制旧文档：

- Embedding 模型变化。
- Embedding 维度变化。
- jieba 分词规则变化。
- Chunk 切分算法变化。
- 需要重新计算的字段变化。

这些情况必须根据新的处理规则重新生成相关字段或向量。

## 18. Mapping 最终配置

最终配置：

- dynamic: strict
- refresh_interval: 30s
- number_of_shards: 1
- number_of_replicas: 0
- Alias: rag_chunks
- 向量类型：int8_hnsw

```json
PUT /rag_chunks_v1
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "refresh_interval": "30s"
  },
  "mappings": {
    "dynamic": "strict",
    "_meta": {
      "schema_version": 1,
      "embedding_model": "BAAI/bge-small-zh-v1.5",
      "embedding_dimensions": 512,
      "tokenizer": "jieba"
    },
    "properties": {
      "document_id": {
        "type": "keyword"
      },
      "document_version_id": {
        "type": "keyword"
      },
      "chunk_index": {
        "type": "integer"
      },
      "content": {
        "type": "text",
        "index": false
      },
      "content_terms": {
        "type": "text",
        "analyzer": "whitespace",
        "search_analyzer": "whitespace"
      },
      "heading": {
        "type": "text",
        "index": false
      },
      "heading_terms": {
        "type": "text",
        "analyzer": "whitespace",
        "search_analyzer": "whitespace"
      },
      "heading_path": {
        "type": "keyword"
      },
      "page_start": {
        "type": "integer"
      },
      "page_end": {
        "type": "integer"
      },
      "content_type": {
        "type": "keyword"
      },
      "image_asset_ids": {
        "type": "keyword"
      },
      "embedding": {
        "type": "dense_vector",
        "element_type": "float",
        "dims": 512,
        "index": true,
        "similarity": "cosine",
        "index_options": {
          "type": "int8_hnsw",
          "m": 16,
          "ef_construction": 100
        }
      }
    }
  },
  "aliases": {
    "rag_chunks": {
      "is_write_index": true
    }
  }
}
```

注意：

1. dynamic strict 会拒绝 Mapping 中未定义的字段。
2. 所有写入字段必须与 Mapping 完全一致。
3. content 和 heading 虽然设置 index=false，但仍保存在 _source 中。
4. content_terms 和 heading_terms 由 Python jieba 生成。
5. refresh_interval=30s 不意味着 Worker 必须等待 30 秒；完成版本写入后应主动 Refresh，再核对数量。
6. 应用统一通过 Alias 访问索引。

## 19. 一致性检查与自动修复

采用方案 A：自动检测 + CLI 手动修复。

### 19.1 一致性原则

SQLite 是文档和解析版本状态的权威来源。

ES 是检索数据存储。

判断有效版本必须读取 SQLite 的 current_version_id，不能仅根据 ES 中是否存在 Chunk 判断。

### 19.2 检查项目

| 检查项 | 检查内容 |
|---|---|
| 当前版本 | current_version_id 对应版本是否存在 |
| Chunk 数量 | ES 中版本 Chunk 数量是否与预期一致 |
| 旧版本残留 | 已切换版本后旧版本是否仍有数据 |
| 删除残留 | deleting 文档是否仍存在 ES 数据 |
| 未完成版本 | 未完成版本是否存在不应保留的 ES 数据 |
| 集群健康 | Cluster 和 Index Health |
| Alias | Alias 是否指向正确物理索引 |

### 19.3 异常处理策略

| 异常 | 第一版处理 |
|---|---|
| 缺少 Chunk | 通过 CLI 补写 |
| 多余 Chunk | 通过 CLI 清理 |
| 删除文档残留 | 通过 CLI 重复删除 |
| 旧版本未清理 | 通过 CLI 清理 |
| 索引结构异常 | 检查并按索引升级流程处理 |

Worker 自动恢复未完成任务。

后台定期执行基础一致性检查。

发现异常时记录日志，不自动修改业务数据。

第一版不增加前端索引管理页面。

### 19.4 CLI 管理建议

建议后续提供：

```bash
# 检查指定文档
python -m app.elasticsearch.admin check-document \
  --document-id doc_xxx

# 检查指定解析版本
python -m app.elasticsearch.admin check-version \
  --version-id version_xxx

# 修复指定解析版本
python -m app.elasticsearch.admin repair-version \
  --version-id version_xxx

# 清理指定文档的残留数据
python -m app.elasticsearch.admin cleanup-document \
  --document-id doc_xxx
```

CLI 必须：

- 先展示检查结果。
- 明确展示拟执行的修复动作。
- 要求用户确认。
- 使用确定性 ID 和幂等操作。
- 记录 operation_logs。
- 不直接修改 SQLite 中的业务权威状态，除非该命令有明确、经过验证的业务恢复规则。

## 20. 第一版不实现的功能

- Elasticsearch ILM 自动生命周期管理。
- 多物理索引并行双写。
- 独立图片向量索引。
- 图片独立向量检索。
- Reranker。
- Multi-Query。
- ES collapse 多样性策略。
- 自动清理全部异常索引数据。
- 前端索引管理页面。
- 自动调整 HNSW 参数。
- 向量相似度硬阈值过滤。

## 21. 开发阶段验收清单

### 索引结构

- [ ] 在 Elasticsearch 8.19.25 创建 rag_chunks_v1。
- [ ] 创建 rag_chunks Alias。
- [ ] 验证 dynamic strict。
- [ ] 验证 512 维 embedding 写入。

### 检索能力

- [ ] 验证 jieba + whitespace BM25。
- [ ] 验证标题权重高于正文。
- [ ] 验证 HNSW kNN。
- [ ] 验证当前解析版本过滤。
- [ ] 验证原生 RRF Retriever。
- [ ] 验证最终 Top 5 多样性处理。

### 写入与删除

- [ ] 验证 AsyncElasticsearch Streaming Bulk。
- [ ] 验证部分失败重试。
- [ ] 验证确定性 Chunk ID 幂等写入。
- [ ] 验证 Refresh 后数量核对。
- [ ] 验证新旧解析版本共存与切换。
- [ ] 验证文档删除和旧版本清理。

### 一致性与性能

- [ ] 验证 Worker 崩溃后的 ES 写入恢复。
- [ ] 验证 deleting 文档不会长期残留 ES 数据。
- [ ] 验证 Alias 原子切换。
- [ ] 验证 512 MB JVM Heap 下的运行情况。
- [ ] 使用真实 PDF 问题集测试 Recall@5、MRR@5 和检索延迟。

---

**文档状态：** ARCH-ES-01 至 ARCH-ES-19 全部确认；具体参数和实现仍需开发阶段验证。