import os
import json
import getpass
import redis
from kafka import KafkaConsumer
from openai import AsyncOpenAI
from agents import Agent, Runner, OpenAIChatCompletionsModel

api_key = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek API Key: ")
client = AsyncOpenAI(base_url="https://api.deepseek.com", api_key=api_key)
model = OpenAIChatCompletionsModel(model="deepseek-chat", openai_client=client)
agent = Agent(name="Assistant", instructions="You are a helpful assistant.", model=model)

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
KEY = "chat"

consumer = KafkaConsumer(
    "demo-topic",
    bootstrap_servers="localhost:9092",
    auto_offset_reset="earliest",
    group_id="demo-group",
)

for msg in consumer:
    user = msg.value.decode("utf-8")
    print("收到用户消息:", user)

    history = [json.loads(m) for m in r.lrange(KEY, 0, -1)]
    result = Runner.run_sync(agent, history + [{"role": "user", "content": user}])
    reply = result.final_output

    r.rpush(KEY, json.dumps({"role": "user", "content": user}))
    r.rpush(KEY, json.dumps({"role": "assistant", "content": reply}))

    print("AI:", reply)
