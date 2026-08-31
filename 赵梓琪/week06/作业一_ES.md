# 作业一

## 1. ES基础链接测试

   `xpack.security.enabled: false` 关闭 Elasticsearch 的安全认证（用户名/密码）

   `network.host: 0.0.0.0` **监听本机所有的网络接口**（IP地址）

   安装完成IK分词插件，ES正常运行

![image-20260831144018848](.\作业.assets\image-20260831144018848.png)

## 2. ES检索、条件过滤

 创建索引（建表）:`es_client.indices.create(index=index_name, body=mapping)`

插入文档：`es_client.index(index=index_name, document=doc)`	插入后 ES 会异步建立倒排索引

刷新索引：`es_client.indices.refresh(index=index_name)`	ES 默认每秒自动刷新一次，手动刷新指令

查询：`response = es_client.search(index=index_name, body=query)`

   

![image-20260831145502701](.\作业.assets\image-20260831145502701.png)

多种检索条件：

![image-20260831150625065](.\作业.assets\image-20260831150625065.png)

使用ES完成向量检索

![image-20260831151142916](.\作业.assets\image-20260831151142916.png)
