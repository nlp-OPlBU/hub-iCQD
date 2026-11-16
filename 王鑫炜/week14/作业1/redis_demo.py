"""Assignment 1: real Redis chat history; run this file twice to see persistence."""
import json
import os
import uuid
from dotenv import load_dotenv
from redis import Redis
load_dotenv()

def main():
    client=Redis.from_url(os.getenv('REDIS_URL','redis://127.0.0.1:16379/0'),decode_responses=True)
    session=os.getenv('DEMO_SESSION','coursework14')
    key='wxw14:demo:chat:'+session
    with client.pipeline(transaction=True) as p:
        p.rpush(key,json.dumps({'role':'user','content':'Redis 如何保存历史对话？','run_id':uuid.uuid4().hex[:8]},ensure_ascii=False),json.dumps({'role':'assistant','content':'通过 List 追加并读取消息，设置 TTL 和长度上限。'},ensure_ascii=False))
        p.ltrim(key,-20,-1);p.expire(key,604800);p.execute()
    print('Redis PING:',client.ping())
    print('Key:',key,'LLEN:',client.llen(key),'TTL:',client.ttl(key))
    print('LRANGE history:')
    for row in client.lrange(key,0,-1): print(json.dumps(json.loads(row),ensure_ascii=False))

if __name__=='__main__':main()
