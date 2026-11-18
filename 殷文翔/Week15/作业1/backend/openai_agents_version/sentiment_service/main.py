from fastapi import FastAPI, Body
import uvicorn
from typing import Annotated
import logging

from agents import Runner

from .agent import sentiment_analysis_agent
from ..config import AnalyzeResult
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
app = FastAPI()

@app.post('/sentiment')
async def sentiment_analysis(input: Annotated[str, Body(embed=True)]):
    logging.info(f"正在进行情感分析:{input}")
    result = await Runner.run(sentiment_analysis_agent,input)
    logging.info(f"分析完成，结果:{result}")
    return AnalyzeResult.model_validate_json(result.final_output)


if __name__ == "__main__":
    uvicorn.run("openai_agents_version.sentiment_service.main:app", host="0.0.0.0", port=8001, reload=True)