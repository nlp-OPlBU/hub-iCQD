# **多模态 RAG 问答系统 — 第一版技术方案（已确定部分）**

第一版技术选型已确定

根据目前的讨论，先固定已经明确的技术选型和功能范围。暂时不展开尚未确定的实现细节。

## **一、项目目标**

基于 Kafka 自己实现一个多模态 RAG 问答系统，实现以下三个核心功能：

1. 支持前端网页上传多个 PDF 文件，通过 Kafka 将任务传递到后端，调用 MinerU API 解析并存储。
2. 支持文档解析状态展示，包括待解析、解析中、解析完成。
3. 支持多模态 RAG 问答，包括：
   - 用户提问，回答支持文字与图片混合展示。
   - 用户同时发送文字和图片，LLM 能结合两者进行回答。

## **二、技术选型**

| 技术        | 选型                | 主要用途           | 部署位置        |
| --------- | ----------------- | -------------- | ----------- |
| 消息队列      | Kafka             | 异步任务传递         | Linux       |
| 检索引擎      | Elasticsearch     | 全文检索、向量检索      | Linux       |
| 关系型数据库    | SQLite            | 关系数据、文档状态      | Mac         |
| PDF 解析    | MinerU API        | PDF 文档解析       | 外部 API      |
| Embedding | BGE-small-zh-v1.5 | 文本向量化          | Mac         |
| LLM       | DeepSeek API      | RAG 问答生成       | 外部 API      |
| 开发语言      | Python            | 后端及业务逻辑        | Mac / Linux |
| 前端        | Web 网页            | 文件上传、状态展示、问答交互 | Mac 本地开发    |

## **三、硬件与部署环境**

| 设备          | 硬件配置                     | 承担任务                     |
| ----------- | ------------------------ | ------------------------ |
| Linux 服务器   | 2 核 CPU / 2GB RAM        | Kafka、Elasticsearch      |
| MacBook Pro | M1 Pro / 16GB RAM / 10 核 | SQLite、Embedding、开发与本地服务 |

## **四、Embedding 模型配置**

[BAAI/bge-small-zh · Hugging Face](https://images.openai.com/static-rsc-4/ibU6Z3slG2eIlDJ_BcJpcV8gNOUO_wjirlQqp3Qkqh4WyVT2JE9ukMWbuAjaLBZB2wcfU8LblNBHH24TLhC3aM3L4MRYR__ut_j_dH6wgS4PzVc1hteLiY1fMtP3CgP5v42zY__OLzUD_VIHgb2d5bgCtaXPOZjkS6Ge_HrfVg4?purpose=inline)

**BAAI/bge-small-zh-v1.5**

Hugging Face 模型

[模型主页](https://huggingface.co/BAAI/bge-small-zh-v1.5?utm_source=chatgpt.com)

| 参数        | 确定值                    |
| --------- | ---------------------- |
| 模型名称      | BAAI/bge-small-zh-v1.5 |
| 向量维度      | 512                    |
| 最大文本长度    | 512 tokens             |
| Python 框架 | Sentence Transformers  |
| 部署设备      | MacBook M1 Pro         |

第一版只使用 Dense Embedding，不引入 Sparse Embedding、ColBERT 或 Reranker。

## **五、已确定的核心数据流**

### **1. PDF 上传与解析**

```
前端上传 PDF
      ↓
后端接收文件
      ↓
Kafka 消息传递
      ↓
MinerU API 解析
      ↓
获取 Markdown、文本、图片
      ↓
文本切片
      ↓
BGE Embedding 向量化
      ↓
Elasticsearch 存储
```

### **2. 文档状态管理**

第一版确定四种状态：

| 状态   | 英文标识       |
| ---- | ---------- |
| 待解析  | pending    |
| 解析中  | processing |
| 解析完成 | completed  |
| 解析失败 | failed     |

SQLite 暂定负责关系数据及文档状态管理。

### **3. 多模态问答**

```
用户提问（文字 / 文字+图片）
             ↓
       后端接收请求
             ↓
      Elasticsearch 检索
             ↓
       获取相关文本和图片
             ↓
       组织多模态上下文
             ↓
        DeepSeek API
             ↓
         生成回答
             ↓
       前端图文混排展示
```

## **六、第一版暂不确定的内容**

以下内容暂时不做最终技术决策，后续根据实际开发需要确定：

| 项目            | 待确定内容                         |
| ------------- | ----------------------------- |
| 文件存储          | PDF 原文件、解析图片的存储位置             |
| Kafka         | Topic 划分、消息格式、重试机制            |
| Elasticsearch | 索引结构、分词器、向量检索参数               |
| 文本切片          | Chunk 大小、重叠长度                 |
| 多模态           | 图片存储、图片与文本关联方式                |
| LLM           | 具体模型及视觉输入方案                   |
| 服务通信          | Mac 与 Linux 之间的接口设计           |
| 数据同步          | SQLite 与 Elasticsearch 的状态一致性 |

**当前技术方案的核心原则：**

1. Kafka 负责异步消息传递。
2. Elasticsearch 负责检索和向量数据存储。
3. SQLite 负责关系数据。
4. MinerU 负责 PDF 解析。
5. BGE 负责文本向量化。
6. DeepSeek 负责问答生成。
7. Mac 承担轻量模型计算，Linux 服务器承担基础服务。

这份内容可以作为项目的第一版技术选型基线。后续确定新内容时，在此基础上增量更新，不需要反复推翻已经确定的部分。