import io
import json
import uuid
import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app import storage, parser, retrieval, main, llm, worker, messaging

def pdf_bytes(text='Redis Kafka MinerU homework'):
    pdf=fitz.open(); page=pdf.new_page(); page.insert_text((50,50),text)
    content=pdf.tobytes();pdf.close();return content

def upload(client, name='sample.pdf'):
    return client.post('/api/documents',files={'files':(name,pdf_bytes(),'application/pdf')})

def ready(doc_id, text='Redis 保存历史对话 Kafka 传递异步解析任务'):
    with storage.db() as c:
        c.execute("UPDATE documents SET status='completed' WHERE id=?",(doc_id,))
        c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',(doc_id+'-0',doc_id,1,'text',text,None))

def test_batch_upload_and_transactional_outbox():
    client=TestClient(main.app)
    result=client.post('/api/documents',files=[('files',('a.pdf',pdf_bytes(),'application/pdf')),('files',('b.pdf',pdf_bytes(),'application/pdf'))])
    assert result.status_code==202
    assert len(result.json()['documents'])==2
    with storage.db() as c:
        assert c.execute('SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL').fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM documents WHERE status='queued'").fetchone()[0]==2

def test_invalid_batch_is_atomic():
    client=TestClient(main.app)
    result=client.post('/api/documents',files=[('files',('a.pdf',pdf_bytes(),'application/pdf')),('files',('b.pdf',b'not pdf','application/pdf'))])
    assert result.status_code==400
    assert storage.documents()==[]

def test_retry_increments_generation_and_rejects_nonfailed():
    client=TestClient(main.app); doc=upload(client).json()['documents'][0]
    assert client.post('/api/documents/'+doc['id']+'/retry').status_code==409
    with storage.db() as c:c.execute("UPDATE documents SET status='failed' WHERE id=?",(doc['id'],))
    result=client.post('/api/documents/'+doc['id']+'/retry')
    assert result.status_code==202 and result.json()['attempt']==2
    with storage.db() as c:assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==2

def test_mineru_image_and_page_are_preserved(isolated_data):
    doc='a'*32; root=isolated_data/'parsed'/doc;root.mkdir()
    Image.new('RGB',(40,40),'green').save(root/'source.png')
    chunks=parser.normalize(doc,root,[{'type':'text','text':'Kafka 保证异步解耦','page_idx':2},
                                      {'type':'image','img_path':'source.png','image_caption':['图1：异步数据路径'],'page_idx':2}])
    assert chunks[0]['page']==3 and chunks[1]['page']==3
    assert chunks[1]['image_url'].endswith('figure-1.png')
    assert (root/'figure-1.png').is_file()

def test_parser_rejects_traversal(isolated_data):
    with pytest.raises(ValueError):parser.safe_child(isolated_data,'../secret.png')
    with pytest.raises(RuntimeError):parser.normalize('a',isolated_data,[])

def test_missing_mineru_never_falls_back(monkeypatch):
    monkeypatch.setenv('MINERU_MODE','cli');monkeypatch.setattr(parser.shutil,'which',lambda _:None)
    with pytest.raises(RuntimeError,match='未安装'):parser.parse_pdf('a'*32)

def test_http_mineru_adapter_preserves_real_image_bytes(isolated_data,monkeypatch):
    import base64
    import httpx
    doc='c'*32
    (isolated_data/'uploads'/f'{doc}.pdf').write_bytes(pdf_bytes())
    image=io.BytesIO();Image.new('RGB',(18,18),'green').save(image,format='PNG')
    content=[{'type':'image','img_path':'images/source.jpg','image_caption':['Kafka任务路径'],'page_idx':0}]
    requests=[]
    def upstream(request):
        requests.append(request)
        return httpx.Response(200,json={'results':{'sample':{'content_list':json.dumps(content),
            'md_content':'# Mocked MinerU API test response',
            'images':{'source.jpg':'data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()}}}})
    original_client=httpx.Client
    monkeypatch.setattr(parser.httpx,'Client',lambda **kwargs:original_client(transport=httpx.MockTransport(upstream),**kwargs))
    monkeypatch.setenv('MINERU_MODE','api');monkeypatch.setenv('MINERU_API_URL','http://mineru-fixture.invalid')
    chunks,name=parser.parse_pdf(doc)
    assert requests[0].url.path=='/file_parse'
    assert b'return_content_list' in requests[0].content
    assert chunks[0]['page']==1 and chunks[0]['image_url']
    path=isolated_data/chunks[0]['image_url'].removeprefix('/assets/')
    with Image.open(path) as im:assert im.size==(18,18) and im.getpixel((0,0))==(0,128,0)

def test_retrieval_scopes_documents_and_no_random_answer():
    client=TestClient(main.app)
    a=upload(client,'a.pdf').json()['documents'][0]['id'];b=upload(client,'b.pdf').json()['documents'][0]['id']
    ready(a,'Redis 保存对话历史');ready(b,'Kafka 传递消息')
    assert retrieval.retrieve('Kafka',[a])==[]
    assert retrieval.retrieve('Kafka',[b])[0]['doc_id']==b
    assert retrieval.retrieve('量子纠缠理论')==[]

def test_assets_do_not_expose_db_or_other_session(isolated_data):
    client=TestClient(main.app)
    assert client.get('/assets/rag.db').status_code==404
    folder=isolated_data/'questions'/('a'*32);folder.mkdir()
    Image.new('RGB',(10,10)).save(folder/'x.png')
    assert client.get('/assets/questions/'+('a'*32)+'/x.png').status_code==404

def test_model_failure_does_not_create_success(monkeypatch):
    client=TestClient(main.app);doc=upload(client).json()['documents'][0]['id'];ready(doc)
    monkeypatch.setattr(llm,'model_configured',lambda:False)
    result=client.post('/api/chat',data={'question':'Redis如何保存历史？'})
    assert result.status_code==503

def test_multimodal_model_receives_real_image_and_filters_untrusted_urls(isolated_data,monkeypatch):
    folder=isolated_data/'parsed'/('b'*32);folder.mkdir();Image.new('RGB',(40,40),'blue').save(folder/'figure.png')
    source={'name':'fixture.pdf','page':1,'text':'Kafka流程图','image_url':'/assets/parsed/'+('b'*32)+'/figure.png'}
    requests=[]
    def mocked_complete(messages,json_mode=False):
        requests.append(messages)
        return json.dumps({'blocks':[{'type':'text','text':'依据[来源1]解释。'},{'type':'image','image_id':'fig1'},
                                     {'type':'image','image_id':'unknown','url':'https://evil.invalid/x'}]}),{'model':'mocked','usage':{},'image_input_count':2}
    monkeypatch.setattr(llm,'complete',mocked_complete)
    url=source['image_url'];blocks,info=llm.answer('解释图',url,[],[source])
    image_parts=[p for p in requests[0][-1]['content'] if p['type']=='image_url']
    assert len(image_parts)==2 and all(x['image_url']['url'].startswith('data:image/png;base64,') for x in image_parts)
    assert len(blocks)==2 and blocks[1]['url']==url

def test_history_replays_user_image_only(isolated_data):
    folder=isolated_data/'questions'/('b'*32);folder.mkdir();Image.new('RGB',(10,10)).save(folder/'x.png')
    result=llm.history_messages([{'role':'user','text':'看图','image_url':'/assets/questions/'+('b'*32)+'/x.png'},
                                 {'role':'assistant','text':'解释'}])
    assert result[0]['content'][1]['type']=='image_url'
    assert isinstance(result[1]['content'],str)

@pytest.mark.integration
def test_real_redis_history_survives_new_client():
    r=messaging.redis_client();key='wxw14:test:'+uuid.uuid4().hex
    try:
        with r.pipeline(transaction=True) as p:p.rpush(key,'user','assistant');p.expire(key,60);p.execute()
        fresh=messaging.redis_client()
        assert fresh.lrange(key,0,-1)==['user','assistant'] and fresh.ttl(key)>0
    finally:r.delete(key)

@pytest.mark.integration
def test_worker_idempotence_and_failure_are_real_redis_locked():
    client=TestClient(main.app);doc=upload(client).json()['documents'][0]['id']
    calls=[]
    def fixture_parser(doc_id):
        calls.append(doc_id)
        return [{'id':doc_id+'-0','doc_id':doc_id,'page':1,'kind':'text','text':'fixture test output','image_url':None}],'test parser (not MinerU)'
    job={'document_id':doc,'attempt':1}
    assert worker.process_job(job,fixture_parser)=='completed'
    assert worker.process_job(job,fixture_parser)=='duplicate' and len(calls)==1
    assert worker.process_job({**job,'attempt':2},fixture_parser)=='duplicate'
    failed=upload(client).json()['documents'][0]['id']
    def error_parser(_):raise RuntimeError('explicit test parser failure')
    assert worker.process_job({'document_id':failed,'attempt':1},error_parser)=='failed'
    assert storage.document(failed)['status']=='failed'

@pytest.mark.integration
def test_real_kafka_outbox_ack_and_duplicate_resend(monkeypatch):
    monkeypatch.setattr(messaging, 'TOPIC', 'wxw14.test.parse.' + uuid.uuid4().hex)
    messaging.ensure_topics()
    client=TestClient(main.app);doc=upload(client).json()['documents'][0]['id']
    p=messaging.producer()
    assert messaging.dispatch_once(p)==1
    with storage.db() as c:
        assert c.execute('SELECT sent_at FROM outbox').fetchone()[0]
        c.execute('UPDATE outbox SET sent_at=NULL')
    assert messaging.dispatch_once(p)==1
    with storage.db() as c:assert c.execute('SELECT COUNT(*) FROM events WHERE event=?',('kafka_published',)).fetchone()[0]==2

@pytest.mark.integration
def test_failed_publish_preserves_outbox(monkeypatch):
    client=TestClient(main.app);upload(client)
    def fail(*args):raise RuntimeError('explicit test broker failure')
    monkeypatch.setattr(messaging,'send',fail)
    with pytest.raises(RuntimeError):messaging.dispatch_once(None)
    with storage.db() as c:
        row=c.execute('SELECT * FROM outbox').fetchone()
        assert row['sent_at'] is None and row['last_error']=='explicit test broker failure'

@pytest.mark.integration
def test_two_rounds_persisted_and_reused_in_real_redis(monkeypatch):
    client=TestClient(main.app);doc=upload(client).json()['documents'][0]['id'];ready(doc)
    monkeypatch.setattr(llm,'model_configured',lambda:True)
    histories=[]
    def query(q,img,h):histories.append(len(h));return q,None
    monkeypatch.setattr(llm,'search_query',query)
    monkeypatch.setattr(llm,'answer',lambda *a:([{'type':'text','text':'mocked model answer'}],{'model':'mocked','usage':{},'image_input_count':0}))
    assert client.get('/api/history').status_code==200
    assert client.post('/api/chat',data={'question':'Redis 历史'}).status_code==200
    assert client.post('/api/chat',data={'question':'那 Kafka 呢？'}).status_code==200
    data=client.get('/api/history').json()
    assert histories==[0,2] and len(data['messages'])==4 and data['storage']=='redis'
    sid=client.cookies.get('wxw14_session');messaging.redis_client().delete('wxw14:chat:'+sid)

@pytest.mark.integration
def test_invalid_image_is_rejected_before_model(monkeypatch):
    client=TestClient(main.app);doc=upload(client).json()['documents'][0]['id'];ready(doc)
    monkeypatch.setattr(llm,'model_configured',lambda:True)
    result=client.post('/api/chat',data={'question':'看图'},files={'image':('fake.png',b'not-image','image/png')})
    assert result.status_code==400

@pytest.mark.integration
def test_model_error_does_not_append_history(monkeypatch):
    client=TestClient(main.app);doc=upload(client).json()['documents'][0]['id'];ready(doc)
    monkeypatch.setattr(llm,'model_configured',lambda:True)
    def fail(*a):raise llm.ModelError('explicit test model failure')
    monkeypatch.setattr(llm,'answer',fail)
    client.get('/api/history')
    result=client.post('/api/chat',data={'question':'Redis'})
    assert result.status_code==502 and client.get('/api/history').json()['messages']==[]
