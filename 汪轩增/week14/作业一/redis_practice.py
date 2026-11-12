import json
import time

import redis

HOST = "127.0.0.1"
PORT = 6379
DB = 0

PREFIX = "hw1:"


def key(name: str) -> str:
    """给键统一加前缀，例如 key('user:1') -> 'hw1:user:1'"""
    return PREFIX + name


def show(title: str) -> None:
    print("\n" + "=" * 46)
    print(title)
    print("=" * 46)


def connect() -> redis.Redis:
    """连接 Redis。decode_responses=True 让返回值是 str 而不是 bytes。"""
    client = redis.Redis(host=HOST, port=PORT, db=DB, decode_responses=True)
    client.ping()  # 连通性检查，连不上会抛 ConnectionError
    return client


def clear_prefix(r: redis.Redis) -> None:
    """删除所有本程序创建的键（使用 scan 而不是 keys，避免阻塞服务器）。"""
    # 先收集再删除：扫描过程中修改键空间可能导致漏删
    for k in list(r.scan_iter(match=PREFIX + "*")):
        r.delete(k)


def demo_string(r: redis.Redis) -> None:
    show("1. String：最基础的键值对")
    k = key("user:1:name")
    r.set(k, "张三")
    print("写入后再读取：", r.get(k))

    r.set(k, "李四")  # 同名键直接覆盖
    print("覆盖后的值：  ", r.get(k))

    # 计数器：浏览量 +1
    views = key("page:views")
    r.set(views, 10)
    r.incr(views)
    r.incrby(views, 5)
    print("初始 10，两次自增后：", r.get(views))

    print("删除该键：", r.delete(k), "（1 表示删除成功）")


def demo_hash(r: redis.Redis) -> None:
    show("2. Hash：适合存一个对象的多个字段")
    user = key("user:100")
    r.hset(user, mapping={"name": "王小明", "age": "20", "city": "北京"})
    print("整个对象：", r.hgetall(user))
    print("只取 name：", r.hget(user, "name"))

    r.hincrby(user, "age", 1)  # 字段自增
    print("过完生日后的 age：", r.hget(user, "age"))


def demo_list(r: redis.Redis) -> None:
    show("3. List：可以当队列 / 栈用")
    queue = key("tasks")
    r.rpush(queue, "任务A", "任务B", "任务C")  # 从右边入队
    print("队列内容：", r.lrange(queue, 0, -1))
    print("长度：    ", r.llen(queue))

    print("从左边取出一个：", r.lpop(queue))
    print("剩余：", r.lrange(queue, 0, -1))


def demo_set(r: redis.Redis) -> None:
    show("4. Set：无序且自动去重")
    tags = key("article:1:tags")
    r.sadd(tags, "python", "redis", "数据库", "python")  # 重复的 python 会被忽略
    print("文章1的标签：", r.smembers(tags))
    print("是否包含 python：", r.sismember(tags, "python"))

    other = key("article:2:tags")
    r.sadd(other, "redis", "缓存")
    print("两篇文章的共同标签：", r.sinter(tags, other))
    print("两篇文章的全部标签：", r.sunion(tags, other))


def demo_zset(r: redis.Redis) -> None:
    show("5. ZSet：带分数的有序集合，排行榜的经典用法")
    board = key("game:rank")
    r.zadd(board, {"玩家A": 90, "玩家B": 120, "玩家C": 75})
    print("按分数从高到低：", r.zrevrange(board, 0, -1, withscores=True))

    r.zincrby(board, 50, "玩家C")  # 玩家C 加 50 分
    rank = r.zrevrank(board, "玩家C") + 1
    print("玩家C 加分后排名第：", rank)
    print("前三名：", r.zrevrange(board, 0, 2))


def demo_expire(r: redis.Redis) -> None:
    show("6. 键过期：短信验证码这类临时数据")
    code = key("sms:code:13800000000")
    r.set(code, "654321", ex=10)  # 10 秒后自动删除
    print("验证码：", r.get(code), "，剩余秒数：", r.ttl(code))

    time.sleep(1)
    print("1 秒后剩余秒数：", r.ttl(code))


def query_database(user_id: int) -> dict:
    """模拟一个很慢的数据库查询（耗时 1 秒）。"""
    time.sleep(1)
    return {"id": user_id, "name": "慢查询用户"}


def get_user_cached(r: redis.Redis, user_id: int) -> dict:
    """缓存读取流程：先查 Redis，没有再去查"数据库"并回写缓存。"""
    cache_key = key(f"cache:user:{user_id}")

    cached = r.get(cache_key)
    if cached is not None:
        print("  -> 缓存命中，直接返回")
        return json.loads(cached)

    print("  -> 缓存未命中，查询数据库……")
    data = query_database(user_id)
    r.set(cache_key, json.dumps(data, ensure_ascii=False), ex=60)  # 缓存 60 秒
    return data


def demo_cache(r: redis.Redis) -> None:
    show("7. 实战小例子：用 Redis 做缓存")

    start = time.time()
    print("第一次查询：")
    print("  结果：", get_user_cached(r, 1))
    print(f"  耗时：{time.time() - start:.2f} 秒")

    start = time.time()
    print("第二次查询：")
    print("  结果：", get_user_cached(r, 1))
    print(f"  耗时：{time.time() - start:.3f} 秒  （明显更快）")


def main() -> None:
    try:
        r = connect()
    except redis.exceptions.ConnectionError:
        print(f"无法连接 Redis（{HOST}:{PORT}），请先启动 Redis 服务。")
        return

    print(f"连接成功：{HOST}:{PORT}，Redis 版本 {r.info('server')['redis_version']}")

    clear_prefix(r)  # 清理上次运行留下的练习数据
    try:
        demo_string(r)
        demo_hash(r)
        demo_list(r)
        demo_set(r)
        demo_zset(r)
        demo_expire(r)
        demo_cache(r)
    finally:
        clear_prefix(r)  # 结束时清理，不污染数据库
        r.close()
        print("\n练习数据已清理，程序结束。")


if __name__ == "__main__":
    main()
