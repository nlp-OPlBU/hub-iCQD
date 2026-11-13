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
    endpoint = urlsplit(settings.llm_base_url)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise SystemExit('Local launcher requires a 127.0.0.1 HTTP LLM_BASE_URL with explicit port')
    if not (settings.llm_model_path / 'config.json').is_file():
        raise SystemExit('LLM_MODEL_PATH is missing model config.json')
    executable = str(Path(sys.executable).parent / 'vllm')
    command = [executable, 'serve', str(settings.llm_model_path),
        '--served-model-name', settings.llm_model, '--host', endpoint.hostname,
        '--port', str(endpoint.port), '--dtype', 'bfloat16',
        '--gpu-memory-utilization', str(settings.llm_gpu_memory_utilization),
        '--max-model-len', str(settings.llm_context_window_tokens),
        '--max-num-seqs', str(settings.llm_max_num_seqs), '--max-num-batched-tokens', '4096',
        '--limit-mm-per-prompt', '{"image":' + str(settings.image_max_context_count) + ',"video":0}',
        '--reasoning-parser', 'qwen3', '--enforce-eager']
    key = settings.llm_api_key.get_secret_value()
    if key and key != 'EMPTY':
        # vLLM reads this from the environment; keep it out of process arguments.
        os.environ['VLLM_API_KEY'] = key
    os.execv(executable, command)
