"""Start the isolated Qwen3-VL relevance scorer using the installed vLLM template."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings


if __name__ == '__main__':
    settings = get_settings()
    endpoint = urlsplit(settings.rerank_base_url)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise SystemExit('Local launcher requires a loopback HTTP RERANK_BASE_URL with explicit port')
    if not (settings.rerank_model_path / 'config.json').is_file() or not settings.rerank_template_path.is_file():
        raise SystemExit('Reranker weights or version-matched scoring template missing')
    executable = str(Path(sys.executable).parent / 'vllm')
    command = [executable, 'serve', str(settings.rerank_model_path),
        '--served-model-name', settings.rerank_model, '--host', endpoint.hostname,
        '--port', str(endpoint.port), '--dtype', 'bfloat16', '--runner', 'pooling',
        '--gpu-memory-utilization', str(settings.rerank_gpu_memory_utilization),
        '--max-model-len', '8192', '--max-num-seqs', '2', '--max-num-batched-tokens', '4096',
        '--limit-mm-per-prompt', json.dumps({'image': 2 * settings.rerank_max_images, 'video': 0}),
        '--hf-overrides', json.dumps({'architectures': ['Qwen3VLForSequenceClassification'],
            'classifier_from_token': ['no', 'yes'], 'is_original_qwen3_reranker': True}),
        '--chat-template', str(settings.rerank_template_path), '--enforce-eager']
    key = settings.rerank_api_key.get_secret_value()
    if key and key != 'EMPTY':
        os.environ['VLLM_API_KEY'] = key
    os.execv(executable, command)
