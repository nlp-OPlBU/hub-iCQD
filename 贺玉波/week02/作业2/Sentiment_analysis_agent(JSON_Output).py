import json
from openai import OpenAI

client = OpenAI(
    api_key="sk-6bc2f2f59a73431caa8954d7b36fb970",
    base_url="https://api.deepseek.com",
)

system_prompt = """
你是人物关系抽取助手。用户会输入一段中文文本，请从中识别所有人物实体与他们之间的明确关系，以 JSON 数组格式输出。

### 字段定义
每个关系对象包含三个字段：
- source: 字符串，关系的发起方人物名称
- relation: 字符串，两人之间的具体关系（如爱慕、同事、父子、朋友等）
- target: 字符串，关系的接收方人物名称

### 输出规则
1. 仅输出纯 JSON 数组，禁止添加任何解释、标题、前缀或 markdown 代码块标记
2. 不遗漏文本中所有明确的人物关系，不添加文本未提及的信息
3. 保证 JSON 格式完全合法，可直接被程序解析

### 示例
输入：小明喜欢小姚，但是小姚喜欢小王。

输出：
[
    {
        "source": "小明",
        "relation": "爱慕",
        "target": "小姚"
    },
    {
        "source": "小姚",
        "relation": "爱慕",
        "target": "小王"
    }
]
"""

# user_prompt = input("请输入测试文本：")
user_prompt = "老王和刘梅是夫妻，他们有一个儿子小明和一个女儿小红。小明和小张是同事，小红暗恋着小张。"

messages = [{"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}]

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages,
    response_format={
        'type': 'json_object'
    }
)

print(json.loads(response.choices[0].message.content))