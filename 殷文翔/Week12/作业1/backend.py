from io import BytesIO
from PIL import Image, UnidentifiedImageError
from typing import Annotated
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from models import Result
import base64
from agents import Runner, ModelBehaviorError
from my_agents import security_analysis_agent

MAX_SIZE = 10 * 1024 * 1024 #10MB

IMAGE_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

app = FastAPI()


@app.post("/analyze_image")
async def analyze_image(
        file: Annotated[UploadFile | None, File()] = None,
        image_url: Annotated[str | None, Form()] = None, ):
    try:
        #校验参数
        #空字符串也视为未提供URL
        image_url = image_url.strip() if image_url else None
        image_url = image_url or None
        if (file is None) == (image_url is None):
            raise HTTPException(400, "请提供 file 或 image_url，且只能提供一个")

        if image_url is not None:
            #只接受HTTP/HTTPS图片地址
            try:
                parsed = urlsplit(image_url)
                valid = (
                        parsed.scheme in {"http", "https"}
                        and bool(parsed.hostname)
                        and parsed.username is None
                        and parsed.password is None
                )
            except ValueError:
                valid = False
            if not valid:
                raise HTTPException(400, "请提供有效的HTTP/HTTPS图片地址")

            image_source = image_url

        #文件处理逻辑
        else:
            content = await file.read(MAX_SIZE)
            if not content:
                raise HTTPException(400, "图片不能为空")
            if len(content) > MAX_SIZE:
                raise HTTPException(413, "图片大小不能超过 10 MB")

            #确认图片类型
            try:
                with Image.open(BytesIO(content)) as image:
                    mime_type = IMAGE_MIME_TYPES.get(image.format)
                    if mime_type is None:
                        raise HTTPException(400, "仅支持JPG,PNG,WEBP类型图片")

                    image.verify()
            except(
                    UnidentifiedImageError,
                    OSError,
                    SyntaxError,
                    ValueError,
                    Image.DecompressionBombError,

            ):
                raise HTTPException(400, "图片无效或已损坏")
            image_base64 = base64.b64encode(content).decode("ascii")
            image_source = f"data:{mime_type};base64,{image_base64}"
        agent_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "请判断图片中的楼道消防安全等级，并描述依据。",
                    },
                    {
                        "type": "input_image",
                        "image_url": image_source,
                        "detail": "auto",
                    },
                ],
            }
        ]
        result = await process_image(agent_input)
        return {"result": result}
    finally:
        if file is not None:
            await file.close()


async def process_image(image_input) -> Result:
    try:
        run_result = await Runner.run(security_analysis_agent, input=image_input)
    except ModelBehaviorError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "模型输出未通过结构化校验：需要包含 classification 和 description 的 JSON。"
                "请重试；若持续失败，请检查模型服务是否支持当前 Responses API、图片输入和 JSON Schema 输出。"
            ),
        ) from exc
    result = run_result.final_output
    if not isinstance(result, Result):
        raise TypeError("模型结构化返回错误")
    print(f"解析成功，解析结果: {result.description}")
    return result
