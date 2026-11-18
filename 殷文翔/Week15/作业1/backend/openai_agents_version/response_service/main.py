from fastapi import FastAPI, Body
import uvicorn
from typing import Annotated
import logging

from agents import Runner

from .agents import encourage_agent, relieve_agent
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
app = FastAPI()


@app.post('/response')
async def response(input: Annotated[str, Body(embed=True)], is_positive: Annotated[bool, Body(embed=True)]):
    logging.info(f"准备回复用户:{input},{is_positive}")

    if is_positive:
        result = await Runner.run(encourage_agent, input)
        return result.final_output
    else:
        result = await Runner.run(relieve_agent, input)
        return result.final_output


if __name__ == "__main__":
    uvicorn.run("openai_agents_version.response_service.main:app", host="0.0.0.0", port=8002, reload=True)
