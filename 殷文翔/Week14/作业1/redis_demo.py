import os
import json
import getpass
import redis
from openai import AsyncOpenAI
from agents import Agent, Runner, OpenAIChatCompletionsModel
import asyncio

api_key = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek API Key: ")
client = AsyncOpenAI(base_url="https://api.deepseek.com", api_key=api_key)
model = OpenAIChatCompletionsModel(model="deepseek-flash", openai_client=client)

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
KEY = "chat"

agent = Agent(name="Assistant", instructions="You are a helpful assistant.", model=model)

async def run_chat():
    while True:
        user = input("You: ")
        if user.strip().lower() in {"exit", "quit"}:
            break
        history = [json.loads(m) for m in r.lrange(KEY, 0, -1)]
        result = await Runner.run(agent, history + [{"role": "user", "content": user}])
        reply = result.final_output
        r.rpush(KEY, json.dumps({"role": "user", "content": user}))
        r.rpush(KEY, json.dumps({"role": "assistant", "content": reply}))
        print(f"AI: {reply}")

asyncio.run(run_chat())
