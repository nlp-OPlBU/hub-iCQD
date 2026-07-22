import json
from openai import OpenAI

client = OpenAI(
    api_key="sk-6bc2f2f59a73431caa8954d7b36fb970",
    base_url="https://api.deepseek.com",
)

# 定义人物关系抽取工具
tools = [
    {
        "type": "function",
        "function": {
            "name": "extract_person_relations",
            "description": "从输入文本中抽取所有明确提及的人物关系，返回结构化的关系数组",
            "parameters": {
                "type": "object",
                "properties": {
                    "relations": {
                        "type": "array",
                        "description": "所有抽取出的人物关系对象",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {
                                    "type": "string",
                                    "description": "关系的发起方人物名称"
                                },
                                "relation": {
                                    "type": "string",
                                    "description": "两人之间的具体关系，如爱慕、同事、父子、朋友等"
                                },
                                "target": {
                                    "type": "string",
                                    "description": "关系的接收方人物名称"
                                }
                            },
                            "required": ["source", "relation", "target"]
                        }
                    }
                },
                "required": ["relations"]
            }
        }
    }
]

system_prompt = """
你是专业的人物关系抽取助手。
你必须调用 `extract_person_relations` 工具来完成任务，禁止直接向用户输出文本回答。
请严格从用户输入的中文文本中，识别所有明确出现的人物实体与他们之间的关系，不得添加文本中未提及的信息，不得遗漏明确存在的关系，将结果通过工具参数返回。
"""


def extract_relations(text: str):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text}
    ]

    response = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=messages,
        tools=tools,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}}
    )
    message = response.choices[0].message
    # print(f"原始回复：{response}")
    # print(f"模型原始回复：{message}")

    if not message.tool_calls or len(message.tool_calls) == 0:
        raise RuntimeError(f"模型未触发关系抽取，原始回复：{message.content}")

    tool_call = message.tool_calls[0]
    try:
        result = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"模型输出参数解析失败：{e}")

    # return result
    return result["relations"]


if __name__ == "__main__":
    # user_prompt = input("请输入测试文本：")
    user_prompt = "老王和刘梅是夫妻，他们有一个儿子小明和一个女儿小红。小明和小张是同事，小红暗恋着小张。"
    relations = extract_relations(user_prompt)
    # print("\n抽取结果：")
    # print(json.dumps(relations, ensure_ascii=False, indent=2))
    print(relations)