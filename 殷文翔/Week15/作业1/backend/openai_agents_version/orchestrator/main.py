from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from requests.exceptions import RequestException, Timeout
import uvicorn
import logging

from .workflow import run

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_methods=["POST"],
)


@app.post("/chat")
async def chat(text: str):
    logging.info(f"用户输入:{text}")
    try:
        result = await run(text)
        logging.info(f"AI回答:{result}")
        return result
    except Timeout as exc:
        raise HTTPException(504, "上游服务响应超时") from exc
    except (RequestException, ValidationError) as exc:
        raise HTTPException(502, "情感识别或回复服务暂时不可用") from exc


if __name__ == "__main__":
    uvicorn.run("openai_agents_version.orchestrator.main:app", host="0.0.0.0", port=8000, reload=True)
