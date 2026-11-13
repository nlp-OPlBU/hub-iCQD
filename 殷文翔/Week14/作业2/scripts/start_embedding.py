"""Launch the project's local vLLM vision endpoint from unified configuration."""
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings


if __name__ == '__main__':
    settings = get_settings()
    endpoint = urlsplit(settings.embedding_base_url)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise SystemExit('Local launcher requires a 127.0.0.1 HTTP EMBEDDING_BASE_URL with explicit port')
    if not (settings.embedding_model_path / 'config.json').is_file():
        raise SystemExit('EMBEDDING_MODEL_PATH is missing model config.json')
    executable = str(Path(sys.executable).parent / 'vllm')
    command = [executable, 'serve', str(settings.embedding_model_path),
        '--served-model-name', settings.embedding_model, '--host', endpoint.hostname,
        '--port', str(endpoint.port), '--dtype', 'bfloat16',
        '--gpu-memory-utilization', str(settings.embedding_gpu_memory_utilization),
        '--max-model-len', '8192',
        '--max-num-seqs', '2', '--max-num-batched-tokens', '4096',
        '--limit-mm-per-prompt', '{"image":' + '1' + ',"video":0}',
        '--runner', 'pooling', '--enforce-eager']
    key = settings.embedding_api_key.get_secret_value()
    if key and key != 'EMPTY':
        # vLLM reads this from the environment; keep it out of process arguments.
        os.environ['VEMBEDDING_API_KEY'] = key
    os.execv(executable, command)
