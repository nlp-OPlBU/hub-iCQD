# Redis 接入

复用环境材料中的 `/workspace/software/redis-7.2.5/redis.conf`，不覆盖该文件。端口、认证和可用数据库编号需要后续检查；配置示例中的 `6379`、DB `0` 为规划默认值。

业务适配入口为 `backend/app/services/redis_cache.py`。用于设置 TTL 的缓存及辅助协调，所有键使用 `multimodal-rag:` 或配置的项目专属前缀。MySQL 是文档和任务状态的持久化来源；缓存丢失应可恢复。

不使用 Redis 替换 Kafka，不清空共享实例，不在当前骨架阶段执行启动或初始化。
