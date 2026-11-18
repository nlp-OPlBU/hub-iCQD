import requests
from ..config import get_settings
from ..config import AnalyzeResult
import logging

config = get_settings()


async def sentiment_analysis(input: str):
    response = requests.post(config.sentiment_analyzer_request_url, json={'input': input}, timeout=30)
    response.raise_for_status()
    analyze_result = AnalyzeResult.model_validate(response.json())
    return analyze_result


async def response(input: str, analyze_result: AnalyzeResult):
    response = requests.post(config.response_request_url,
                             json={'input': input, 'is_positive': analyze_result.result == "positive"},timeout=30)
    response.raise_for_status()
    analyze_result = response.json()
    return analyze_result


async def run(input: str):
    analyze_result = await sentiment_analysis(input)
    response_result = await response(input, analyze_result)
    return response_result
