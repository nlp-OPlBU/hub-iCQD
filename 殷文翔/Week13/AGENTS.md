# 项目协作指南

## 适用范围

本文件适用于 `Week13/` 及其子目录。面向在此项目中工作的编码 Agent 和维护者；项目使用方法见 [REMADE.md](REMADE.md)。默认使用简体中文沟通、编写说明，代码标识符延续英文命名。

修改前先阅读相关源码及当前差异。此目录位于更大的 Git 仓库中，检查状态时使用 `git status --short -- .`，不要处理其他周的文件。保留用户已有的暂存、未暂存修改，不执行回滚、重置或无关格式化。

## 项目定位与结构

项目将自然语言问题转换为 SQLite 查询，并返回包含 SQL、答案和证据的 JSON。当前运行实现使用 LangChain `create_agent`、`ChatOpenAI`、`ToolStrategy` 和 Pydantic，流程由 Python 显式编排。

| 路径 | 职责 |
| --- | --- |
| `src/sql_agent/cli.py`、`__main__.py` | 命令行输入、日志初始化、JSON 输出与退出码 |
| `src/sql_agent/config.py` | 环境变量、默认值和数据库路径解析 |
| `src/sql_agent/models.py` | Agent 结构化输出、内部状态和最终输出的数据契约 |
| `src/sql_agent/workflow.py` | 选表、生成、校验、执行、有限修复、汇总及降级 |
| `src/sql_agent/agents/` | 四个 Agent 的模块级定义和提示词 |
| `src/sql_agent/db/catalog.py` | 数据库目录、列统计、样例、外键关系和进程级缓存 |
| `src/sql_agent/db/validator.py` | SQL 规范化、安全检查及 `EXPLAIN` 编译校验 |
| `src/sql_agent/db/executor.py` | 只读执行、超时、结果行数与单元格长度限制 |
| `ref/` | Chinook 数据库及教学参考代码 |
| `tests/` | 预留测试目录，目前没有测试文件 |

`ref/agent-demo.py` 使用的是另一套 SDK，仅供理解显式编排方式。不要把参考文件的依赖、占位密钥、模型调用方式直接复制到主程序。当前 `table_selector.py` 虽导入了 LangGraph 符号，但业务流程未使用 `StateGraph` 编排。

## 修改原则

- 保持 Agent 职责分离：选表不生成 SQL，生成不执行 SQL，改写依据实际错误，汇总只解释提供的结果。
- 流程决策、重试上限、最终 JSON 组装由代码控制，不交给提示词决定。
- 修改数据字段时同步检查 Pydantic 模型、Agent 的 `response_format`、工作流、CLI 和说明文档。
- 环境配置集中在 `config.py`；不要在 Agent 模块中散落新的配置解析逻辑。配置和模型在导入时初始化，测试或脚本应在导入包之前设置环境变量。
- 延续类型注解、异步 `ainvoke` 调用和结构化输出类型检查；使用 `logging`，不要以 `print` 输出调试信息。
- 标准输出留给最终 JSON，日志和交互提示写入标准错误。`output.error` 非空时 CLI 退出码为 1；需要澄清或空结果不能随意改成异常。
- 不因本地目录叫 Week13 就跨目录搬运或重构其他周的代码。依赖新增、配置变更和行为变化应有明确任务依据。

## 数据库与结果约束

- 保持所有数据库连接只读：SQLAlchemy URL 使用 `mode=ro`，执行器使用 SQLite 只读 URI 和 `PRAGMA query_only = ON`。不要修改或重建 `ref/chinook.db`。
- 生成 SQL 必须先经过 `SQLValidator`；只允许单条 `SELECT` 或 `WITH ... SELECT`，不能通过删除禁用词检查、多语句检查或只读设置来修复错误。
- 校验使用 `EXPLAIN` 编译 SQL，不执行实际查询；执行留给 `SQLExecutor`。不要复用参考 Notebook 中可能实际执行 SQL 的检查方法。
- 表名、字段、外键以实际数据库为准。Chinook 表名为小写，例如 `tracks`、`invoice_items`；字段使用如 `TrackId` 的命名，不能把所有标识符都假定为 PascalCase。
- 选表需要验证表存在且外键关系连通。修复需保留原问题含义和失败历史，并保持有限次数；空结果不重试。
- 保留执行超时、结果行数和单元格长度限制。汇总失败时保留表格降级；不得编造证据、统计数值或未执行的查询。
- `row_count` 是实际保留的结果行数；`truncated` 表示执行器行数截断。汇总另有行数上限，不应将局部结果描述为完整数据。
- 不把 API 密钥写入源码、文档、Notebook 输出、日志或提交。问题、schema 样例和查询结果会传给配置的模型端点；测试优先使用随项目提供的示例库或临时库。

## 运行与验证

完整环境配置见 `REMADE.md`。项目采用 `src/` 布局，但目前没有 `pyproject.toml`、`requirements.txt` 或锁文件，不要声称支持 `pip install -e .`。

在项目根目录的 PowerShell 中，设置实际使用的 Python 环境以及：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m compileall -q src/sql_agent
```

语法检查不需要模型密钥。运行入口和导入包可能创建模块级模型对象，因此 `--help` 也需要已安装运行依赖，并可能需要预先配置密钥。

根据改动选择验证范围：

- 纯文档变更：核对路径、参数、默认值和命令与源码一致，无需调用外部模型。
- 数据库层变更：用临时 SQLite 库验证查询、非法 SQL、引号与注释、多语句、未知字段、截断和超时中与改动相关的行为，不改示例库。
- 工作流变更：模拟 Agent 的异步返回，覆盖相关成功、澄清、修复耗尽、`cannot_fix`、空结果或汇总降级分支；不要让单元测试依赖真实 API。
- CLI 或输出契约变更：检查标准输出为可解析 JSON、日志走标准错误，以及错误退出码。
- 新增 pytest 测试后，在相同环境运行 `python -m pytest tests -q`。当前空测试目录并不代表测试通过，pytest 的“未收集到测试”也不是通过结果。

无需为纯说明或其他低影响可逆变更新增测试。没有依赖、网络或密钥时，应明确说明未验证的部分，不得将语法检查等同于端到端运行成功。

## 交付要求

总结实际修改、验证结果和剩余限制。涉及启动方法、环境变量、数据契约或用户可见行为时，同步更新 `REMADE.md`。避免添加与本次任务无关的依赖、生成产物或数据库副本。
