from fastapi import FastAPI, Body
import uvicorn
from typing import Annotated
import logging

from .responder import responder_encourage_agent, responder_relieve_agent

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
app = FastAPI()


@app.post('/response')
async def response(input: Annotated[str, Body(embed=True)], is_positive: Annotated[bool, Body(embed=True)]):
    logging.info(f"准备回复用户:{input},{is_positive}")

    if is_positive:
        result = await responder_encourage_agent.ainvoke({"messages": [{"role": "user", "content": input}]})
        return result["messages"][-1].content
    else:
        result = await responder_relieve_agent.ainvoke({"messages": [{"role": "user", "content": input}]})
        return result["messages"][-1].content


if __name__ == "__main__":
    uvicorn.run("langgraph_version.response_service.main:app", host="0.0.0.0", port=8002, reload=True)
