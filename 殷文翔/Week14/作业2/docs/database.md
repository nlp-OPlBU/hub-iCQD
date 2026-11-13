# MySQL 数据结构与迁移

## 迁移是什么意思

这里的“迁移”是**给数据库结构做版本管理**，不是把 MySQL 搬到另一台机器。例如第一次建 documents 表，下一次给它增加一个字段；每次变化保存为一个可审阅的脚本，测试环境、开发环境和服务器依次执行同样的脚本，避免手工改表造成差异。

本项目使用 SQLAlchemy 定义 Python ORM 数据结构，Alembic 管理迁移历史。ORM 模型不会自动改变已有数据库。`upgrade()` 描述升级，`downgrade()` 描述回退，`revision/down_revision` 把脚本串成版本链；数据库内的 `alembic_version` 表记录已应用版本。当前只有 `0001_initial`，建 6 张业务表；再加 Alembic 自己的版本表，总计 7 张。

迁移脚本是冻结快照，不能运行时导入最新 ORM 再 create_all，否则旧迁移会随新代码变化。已应用的迁移不要直接改，应新增下一版。API 启动不会迁移，避免多进程同时变更结构。

## 表与约束

| 表 | 主要数据和约束 |
| --- | --- |
| documents | 稳定 UUID、原名、内部 storage_key、SHA256、字节数、latest_version、可空 current_searchable_version；不重复保存易失配的任务状态 |
| parse_tasks | UUID、document_id/version 唯一、status/stage、attempts/max_retries、错误、下次尝试、租约持有者/令牌/到期/心跳、产物 key、索引名/索引时间、检索模式；document_id/retry_key 唯一 |
| outbox_events | 事件 UUID、task_id/document_id/version 复合外键、事件类型/Topic/payload、投递状态、尝试次数/预算、可投递时间、租约、发送确认时间、错误；task_id/event_type 唯一 |
| conversations | UUID、标题、next_sequence、创建/更新时间；短事务锁定序号分配，模型请求在事务外执行 |
| messages | UUID、conversation_id、sequence、request_key/request_hash、role/status、Markdown、image_ids、实际 document_scope 快照、来源 JSON、模型及错误；会话+序号唯一，会话+请求键+角色唯一 |
| image_resources | UUID、内部存储 key、类型、MIME、尺寸/大小/SHA256；文档图绑定 document_id/version/page，用户图绑定 conversation_id；CHECK 保证二者互斥 |

统一 InnoDB、utf8mb4，时间用 DATETIME(6) 存 UTC。默认值由 ORM 创建对象时填充；直接 SQL INSERT 必须提供全部非空字段，不能假定数据库有未声明的服务端默认值。JSON 中不能存 Base64、凭据或绝对路径。原文件 SHA256 不设唯一约束：同一内容可作为两个独立文档上传。

任务的文档外键先建，再用 ALTER TABLE 添加文档的可检索版本复合外键，解决 documents↔parse_tasks 的循环依赖。回滚时先移除此外键，再按依赖倒序删表。外键限制版本归属，状态转换及“completed 才可检索”仍需后续 repository 事务实现，详见 [API 契约](api.md)。已实现上传 repository 的文档/v1 pending 任务/outbox 原子插入、文档/任务查询、人工重试新版本，以及基于状态/租约令牌/latest_version 的 Worker 条件更新和 outbox 投递租约。租约、重试、失败事件复用现有列，本轮不改变表结构或迁移版本。

至少需要 **MySQL 8.0.16**（依赖真正执行的 CHECK 约束）；本机验收版本与结果见 [verification.md](verification.md)。服务器 MySQL 版本仍待确认。

## 首次安装与执行

1. 在项目根目录激活系统 conda 环境 `py312`，安装依赖：

   ```bash
   conda activate py312
   python -m pip install -r backend/requirements-dev.txt
   cp -n .env.example .env
   ```

   已有 `.env` 时保留它，不重复覆盖。生产运行只需 requirements.txt；完整本地可复现版本另见 requirements-lock.txt。

2. 在 `.env` 填写 MYSQL_HOST、MYSQL_PORT、MYSQL_DATABASE、MYSQL_USER、MYSQL_PASSWORD，使用项目专用库和账号。先由管理员在明确授权下创建匹配名称的空库和账号，并授予该库所需权限。Alembic 只连接已有数据库，不创建账号，不修改全局 MySQL 配置。

   以下是管理员参考模板，需替换尖括号内容，不能原样执行或把密码提交到 Git；部署时可用独立迁移账号授予 DDL 权限，API 账号仅 DML 权限：

   ```sql
   CREATE DATABASE `<MYSQL_DATABASE>` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER '<MYSQL_USER>'@'<允许连接的主机>' IDENTIFIED BY '<本地生成的强密码>';
   GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
     ON `<MYSQL_DATABASE>`.* TO '<MYSQL_USER>'@'<允许连接的主机>';
   ```

3. 在 backend 目录使用同一 conda 环境执行：

   ```bash
   cd backend
   python -m alembic upgrade head --sql
   python -m alembic upgrade head
   python -m alembic current
   python -m alembic check
   ```

   第一条只生成可审阅 SQL，不连接数据库；第二条才实际建表。current 应显示 `0001_initial (head)`。重复执行 upgrade head 不重复建表。也可执行项目 `scripts/init_mysql.sh`（只升级配置中的数据库）。

4. 启动 `python -m app.main`，访问 `/api/health/ready` 核对 MySQL 和迁移版本。无数据库也可启动并调用 `/api/health`，ready 会返回 503，不能把它当数据库通过。

## 上传文件补偿

文件系统与 MySQL 无法共用事务。上传先写入并 fsync `DATA_DIR/tmp/upload-recovery/<document_id>.json`（仅含内部文档/任务 ID 和 storage_key），再按内部 UUID 保存 PDF 并 fsync；随后同一个 MySQL 事务按文档、任务、outbox 顺序 flush，提交成功后移除补偿记录。请求体临时文件随请求退出清理。

- COMMIT 之前失败：回滚事务并尝试删掉该文件及补偿记录。文件删除失败时保留记录，便于重试清理；已成功提交的其他文件不受影响。
- COMMIT 报错：可能是提交成功但确认丢失。新主库会话确认到同一文档和任务则返回 accepted；否则返回逐文件 `UPLOAD_COMMIT_UNCERTAIN`，保留原文件与补偿记录。此时不自动重传，也不凭一次查无结果删除文件。
- 提交成功但补偿记录删除失败或进程中断：原文件保留，后续核对主库即可移除多余记录。

恢复工具默认只读预览。**执行补偿前须停止所有 API/上传写入进程并等待在途事务结束**，使用与上传相同的 DATA_DIR 和主库配置；不要连接可能滞后的只读副本。不要在 API 启动时或按文件年龄自动清理，避免误删尚在提交的文件。当前没有自动补偿调度。

在 backend 目录、激活 conda 环境 `py312` 后执行：

```powershell
python -m app.services.upload_recovery
python -m app.services.upload_recovery --apply --uploads-stopped
```

工具只处理 upload-recovery 目录中身份和路径校验通过的记录。主库仍有对应文档且文件存在：保留 PDF，只删除补偿记录；主库无文档：删除孤立 PDF，再删除记录；数据库不可用、记录损坏、存储 key 不匹配或文件异常：保留材料并报告 errors（退出码 1），供人工排查。预览不创建/删除业务记录。它不扫描或删除其他上传文件、解析产物或共享服务数据，也不替代主库和共享存储的备份/断电恢复。

## 后续增加字段如何实现

1. 修改 `backend/app/models/` 中的模型。
2. 在开发专用数据库已升级到旧 head 的前提下，运行 `python -m alembic revision --autogenerate -m "add document field"`。
3. 审查生成的脚本，尤其重命名、数据回填、NOT NULL、索引与循环外键。自动生成不是替代审查，不能可靠推断字段重命名。
4. 先对专属测试库执行 upgrade、必要的数据校验和 downgrade/再次 upgrade；通过后备份目标库，再按部署安排升级。同步更新健康检查期望版本、API 类型及文档。

`python -m alembic downgrade -1` 会执行上一版本的 downgrade；当前首版 downgrade 会删除全部业务表和数据，只可用于可丢弃测试库或经备份和明确安排的回退。MySQL DDL 通常隐式提交，迁移中途失败不能假定整批自动回滚；停止后审阅实际结构并修复，不随意 stamp 跳过失败。

参考：[Alembic 官方教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)、[离线 SQL 生成](https://alembic.sqlalchemy.org/en/latest/offline.html)。

## 会话、图片与来源实现

会话创建/分页、消息分页及问答幂等已接入现有表，无结构变更。每轮用户/助手消息及 document_scope 版本快照同事务提交，结束时保存助手 Markdown、实际来源 JSON 或失败错误。同一会话并发请求通过行锁分配序号；同 key 不同问题冲突，已完成/失败结果直接重放。过期 pending 在查询时转为 REQUEST_INTERRUPTED，恢复规则见 API 契约。

用户原图绑定 conversation_id；命中文档图片按版本和归一化产物页码校验后，以 UUID v5 登记 image_resources。来源 JSON 包含 source_id、文档 UUID/版本/原名、实际页码、分块 ID 与图片元数据/受控 URL，不保存绝对路径或 Base64。文档后续重试不改写已保存来源，也不删除旧图片。

图片上传明确回滚时清理文件；提交结果不明时保留原图并尝试主库复查，否则返回 IMAGE_UPLOAD_COMMIT_UNCERTAIN 和资源 ID。尚无图片孤立文件自动补偿工具，维护者需在停写后核对主库，不能按年龄自动删除。
