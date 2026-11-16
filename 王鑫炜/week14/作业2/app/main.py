import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import fitz
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from . import storage, llm
from .config import DATA, ROOT, MAX_PDF, MAX_IMAGE, MAX_PAGES, TOPIC, BROKERS
from .messaging import redis_client, dispatch_loop
from .retrieval import retrieve

@asynccontextmanager
async def lifespan(app):
    storage.init()
    stop = threading.Event()
    thread = threading.Thread(target=dispatch_loop, args=(stop,), daemon=True)
    thread.start()
    yield
    stop.set()
    await asyncio.to_thread(thread.join, 12)

app = FastAPI(title='知页 · 多模态文档问答', lifespan=lifespan)
app.mount('/web', StaticFiles(directory=ROOT / 'web'), name='web')

def session(request, response):
    value = request.cookies.get('wxw14_session', '')
    if not re.fullmatch(r'[a-f0-9]{32}', value):
        value = uuid.uuid4().hex
        response.set_cookie('wxw14_session', value, httponly=True, samesite='lax', max_age=604800)
    return value

def redis_or_503():
    try:
        client = redis_client()
        client.ping()
        return client
    except Exception:
        raise HTTPException(503, 'Redis 不可用，请先启动服务；历史记录没有改存到内存')

def public_doc(item):
    return {**item, 'status_label': {'queued': '待解析', 'processing': '解析中',
                                    'completed': '解析完成', 'failed': '解析失败'}[item['status']]}

@app.get('/')
def index():
    return FileResponse(ROOT / 'web' / 'index.html')

@app.get('/api/health')
def health():
    from confluent_kafka.admin import AdminClient
    try:
        r = redis_client()
        r.ping()
        redis_ok = True
        heartbeat = r.get('wxw14:worker:heartbeat')
    except Exception:
        redis_ok, heartbeat = False, None
    try:
        kafka_ok = bool(AdminClient({'bootstrap.servers': BROKERS, 'socket.timeout.ms': 2000}).list_topics(timeout=2).brokers)
    except Exception:
        kafka_ok = False
    return {'redis': redis_ok, 'kafka': kafka_ok, 'worker': bool(heartbeat),
            'worker_last_seen': heartbeat, 'llm': llm.model_configured(),
            'model': os.getenv('LLM_MODEL', 'deepseek-flash'), 'parser': 'MinerU', 'topic': TOPIC}

@app.get('/api/documents')
def list_documents():
    return {'documents': [public_doc(x) for x in storage.documents()]}

@app.post('/api/documents', status_code=202)
async def upload(files: list[UploadFile] = File(...)):
    if not 1 <= len(files) <= 10:
        raise HTTPException(400, '一次请选择1至10份PDF')
    validated = []
    try:
        for f in files:
            content = await f.read(MAX_PDF + 1)
            if len(content) > MAX_PDF:
                raise HTTPException(413, '单份PDF不得超过30MB')
            if not f.filename or not f.filename.lower().endswith('.pdf') or not content.startswith(b'%PDF-'):
                raise HTTPException(400, '只支持有效的PDF文件')
            try:
                with fitz.open(stream=content, filetype='pdf') as pdf:
                    if pdf.is_encrypted or not 1 <= pdf.page_count <= MAX_PAGES:
                        raise ValueError('pages or encryption')
                    pages = pdf.page_count
            except Exception:
                raise HTTPException(400, 'PDF损坏、已加密或超过100页限制')
            name = Path(f.filename.replace('\\', '/')).name[:200]
            validated.append((name, content, pages))
    finally:
        for f in files:
            await f.close()
    created = []
    paths = []
    try:
        with storage.db() as c:
            for name, content, pages in validated:
                doc_id = uuid.uuid4().hex
                path = DATA / 'uploads' / f'{doc_id}.pdf'
                path.write_bytes(content)
                paths.append(path)
                timestamp = storage.now()
                c.execute('INSERT INTO documents(id,name,sha256,size,pages,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                          (doc_id, name, hashlib.sha256(content).hexdigest(), len(content), pages, 'queued', timestamp, timestamp))
                storage.enqueue(c, doc_id, 1)
                created.append(doc_id)
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    return {'documents': [public_doc(storage.document(i)) for i in created]}

@app.post('/api/documents/{doc_id}/retry', status_code=202)
def retry(doc_id: str):
    with storage.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, '文档不存在')
        if row['status'] != 'failed':
            raise HTTPException(409, '只有解析失败的文档可以重试')
        attempt = row['attempt'] + 1
        c.execute("UPDATE documents SET status='queued',attempt=?,error=NULL,updated_at=? WHERE id=?", (attempt, storage.now(), doc_id))
        storage.enqueue(c, doc_id, attempt)
    return public_doc(storage.document(doc_id))

@app.get('/api/events')
def events():
    with storage.db() as c:
        rows = [dict(x) for x in c.execute('SELECT events.*,documents.name FROM events LEFT JOIN documents ON documents.id=events.doc_id ORDER BY events.id DESC LIMIT 80')]
        outbox = [dict(x) for x in c.execute('SELECT event_id,doc_id,sent_at,last_error FROM outbox ORDER BY rowid DESC LIMIT 40')]
    return {'events': rows, 'outbox': outbox}

@app.get('/api/history')
def history(request: Request, response: Response):
    sid = session(request, response)
    r = redis_or_503()
    return {'messages': [json.loads(x) for x in r.lrange('wxw14:chat:' + sid, 0, -1)],
            'ttl_seconds': r.ttl('wxw14:chat:' + sid), 'storage': 'redis'}

@app.post('/api/session')
def new_session(response: Response):
    # Keep previous Redis history; create a fresh browser session instead of deleting data.
    response.set_cookie('wxw14_session', uuid.uuid4().hex, httponly=True, samesite='lax', max_age=604800)
    return {'ok': True}

@app.get('/assets/{relative:path}')
def assets(relative: str, request: Request):
    path = (DATA / relative).resolve()
    if not path.is_relative_to(DATA) or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'} or not path.is_file():
        raise HTTPException(404, '图片不存在')
    if relative.startswith('questions/'):
        sid = request.cookies.get('wxw14_session', '')
        if not re.fullmatch(r'[a-f0-9]{32}', sid) or not path.is_relative_to(DATA / 'questions' / sid):
            raise HTTPException(404, '图片不存在')
    elif not relative.startswith('parsed/'):
        raise HTTPException(404, '图片不存在')
    return FileResponse(path, headers={'Cache-Control': 'private, max-age=3600'})

@app.get('/api/documents/{doc_id}/pdf')
def pdf(doc_id: str):
    item = storage.document(doc_id)
    if not item:
        raise HTTPException(404, '文档不存在')
    return FileResponse(DATA / 'uploads' / f'{doc_id}.pdf', media_type='application/pdf', filename=item['name'])

@app.post('/api/chat')
def chat(request: Request, response: Response, question: str = Form(...),
         document_ids: str = Form('[]'), image: UploadFile | None = File(None)):
    started = time.monotonic()
    question = question.strip()
    if not 1 <= len(question) <= 2000:
        raise HTTPException(400, '请输入1至2000字的问题')
    try:
        selected = json.loads(document_ids)
        if not isinstance(selected, list) or len(selected) > 100 or any(not isinstance(x, str) for x in selected):
            raise ValueError()
    except ValueError:
        raise HTTPException(400, '文档选择格式错误')
    ready = {x['id'] for x in storage.documents() if x['status'] == 'completed'}
    if not ready or any(x not in ready for x in selected):
        raise HTTPException(409, '请等待所选文档解析完成')
    if not llm.model_configured():
        raise HTTPException(503, '请先在服务端配置视觉模型密钥')
    sid = session(request, response)
    r = redis_or_503()
    lock = r.lock('wxw14:chat-lock:' + sid, timeout=420, blocking_timeout=0)
    if not lock.acquire():
        raise HTTPException(409, '本会话正在回答，请等待本轮完成')
    image_path = None
    succeeded = False
    try:
        image_url = None
        if image:
            data = image.file.read(MAX_IMAGE + 1)
            if len(data) > MAX_IMAGE:
                raise HTTPException(413, '图片不得超过8MB')
            try:
                with Image.open(io.BytesIO(data)) as im:
                    if im.format not in {'PNG', 'JPEG', 'WEBP'} or im.width * im.height > 20000000:
                        raise ValueError('invalid image')
                    from PIL import ImageOps
                    im = ImageOps.exif_transpose(im).convert('RGB')
                    im.thumbnail((1600, 1600))
                    folder = DATA / 'questions' / sid
                    folder.mkdir(exist_ok=True)
                    image_path = folder / (uuid.uuid4().hex + '.png')
                    im.save(image_path)
                    image_url = '/assets/questions/' + sid + '/' + image_path.name
            except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise HTTPException(400, '请选择有效的PNG、JPEG或WebP图片（不超过2000万像素）')
        key = 'wxw14:chat:' + sid
        old = [json.loads(x) for x in r.lrange(key, -10, -1)]
        query, rewrite_info = llm.search_query(question, image_url, old)
        sources = retrieve(query, selected or None)
        blocks, info = llm.answer(question, image_url, old, sources)
        user = {'role': 'user', 'text': question, 'image_url': image_url, 'created_at': storage.now()}
        result = {'role': 'assistant', 'text': '\n\n'.join(x['text'] for x in blocks if x['type'] == 'text'),
                  'blocks': blocks, 'sources': sources, 'created_at': storage.now(),
                  'model': info['model'], 'usage': info['usage'],
                  'image_input_count': info['image_input_count'],
                  'history_messages': len(old), 'retrieval_query': query,
                  'rewrite_usage': rewrite_info, 'elapsed_ms': round((time.monotonic() - started) * 1000)}
        with r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(user, ensure_ascii=False), json.dumps(result, ensure_ascii=False))
            pipe.ltrim(key, -100, -1)
            pipe.expire(key, int(os.getenv('CHAT_TTL_SECONDS', '604800')))
            pipe.execute()
        succeeded = True
        return {'user': user, 'answer': result, 'storage': 'redis'}
    except llm.ModelError as e:
        raise HTTPException(502, str(e))
    finally:
        if image:
            image.file.close()
        if image_path and not succeeded:
            image_path.unlink(missing_ok=True)
        lock.release()
