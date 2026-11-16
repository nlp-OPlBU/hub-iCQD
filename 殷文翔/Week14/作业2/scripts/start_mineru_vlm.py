"""Start MinerU's version-matched local VLM using the CLI's configured endpoint."""
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings


if __name__ == '__main__':
    settings = get_settings()
    config = yaml.safe_load(settings.mineru_config.read_text(encoding='utf-8'))
    model = config['model']['vlm']
    endpoint = urlsplit(model['server_url'])
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise SystemExit('MinerU local launcher requires an explicit loopback server_url')
    os.environ['MINERU_HOME'] = str(settings.mineru_home)
    os.environ['MINERU_CONFIG'] = str(settings.mineru_config)
    if settings.mineru_offline:
        os.environ['HF_HUB_OFFLINE'] = '1'
    executable = settings.mineru_command
    command = [executable, 'vlm-server', '--engine', 'vllm', '--host', endpoint.hostname,
        '--port', str(endpoint.port), '--served-model-name', model['model'],
        '--dtype', 'bfloat16', '--gpu-memory-utilization', str(settings.mineru_vlm_gpu_memory_utilization),
        '--max-model-len', str(settings.mineru_vlm_max_model_len),
        '--max-num-seqs', str(settings.mineru_vlm_max_num_seqs),
        '--max-num-batched-tokens', '4096', '--enforce-eager']
    os.execvp(executable, command)
