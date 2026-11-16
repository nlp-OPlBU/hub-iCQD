import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'runtime'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
for name in ('uploads', 'parsed', 'questions'):
    (DATA / name).mkdir(exist_ok=True)
REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:16379/0')
BROKERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', '127.0.0.1:19092')
TOPIC = os.getenv('KAFKA_TOPIC', 'wxw14.pdf.parse')
GROUP = os.getenv('KAFKA_GROUP', 'wxw14-mineru')
PARSE_TIMEOUT = int(os.getenv('PARSE_TIMEOUT', '900'))
MAX_PDF = 30 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024
MAX_PAGES = 100
