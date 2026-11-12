"""Export this project's real runtime evidence without environment secrets."""
from datetime import datetime
import json
from pathlib import Path
import shutil
from app.config import DATA, ROOT
from app.messaging import redis_client
from app.storage import db, documents

docs=ROOT/'docs'
docs.mkdir(exist_ok=True)
def save(name, value):
    (docs/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))

items=documents()
with db() as c:
    events=[dict(r) for r in c.execute('SELECT events.*,documents.name FROM events JOIN documents ON events.doc_id=documents.id ORDER BY events.id')]
    outbox=[dict(r) for r in c.execute('SELECT event_id,doc_id,sent_at,last_error FROM outbox ORDER BY rowid')]
save('rag-pipeline.json',{'captured_at':datetime.now().astimezone().isoformat(),'documents':items,'events':events,'outbox':outbox})
r=redis_client();conversations=[]
for key in r.scan_iter('wxw14:chat:*'):
    messages=[json.loads(x) for x in r.lrange(key,0,-1)]
    if any(x.get('model')=='deepseek-flash' for x in messages):
        conversations.append({'storage':'Redis List','message_count':len(messages),'ttl_seconds':r.ttl(key),'messages':messages})
save('rag-real-chat.json',conversations)
for item in items:
    if item['status']!='completed':continue
    source=DATA/'parsed'/item['id']
    target=docs/'mineru-output'/Path(item['name']).stem
    target.mkdir(parents=True,exist_ok=True)
    for p in source.rglob('*_content_list.json'):
        shutil.copy2(p,target/'content_list.json')
        for md in p.parent.glob('*.md'):shutil.copy2(md,target/'document.md')
        if (p.parent/'images').exists():shutil.copytree(p.parent/'images',target/'images',dirs_exist_ok=True)
    for p in source.glob('figure-*.png'):shutil.copy2(p,target/p.name)
    # MinerU logs are own-document execution logs; save only the final analysis summary.
    log=source/'mineru.log'
    if log.exists():
        lines=log.read_text(errors='replace').replace('\r','\n').splitlines()
        summary=[line for line in lines if 'INFO' in line or 'ERROR' in line or '100%' in line]
        (target/'parse-summary.txt').write_text('\n'.join(summary[-25:]))
print('Evidence exported:',len(items),'documents;',len(conversations),'real model conversations')
