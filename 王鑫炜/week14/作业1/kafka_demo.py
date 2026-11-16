"""Assignment 1: real Kafka produce, consume, then explicitly commit the offset."""
import json
import os
import time
import uuid
from dotenv import load_dotenv
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic
load_dotenv()

def main():
    brokers=os.getenv('KAFKA_BOOTSTRAP_SERVERS','127.0.0.1:19092')
    topic='wxw14.message.demo'
    admin=AdminClient({'bootstrap.servers':brokers})
    if topic not in admin.list_topics(timeout=10).topics:
        admin.create_topics([NewTopic(topic,1,1)])[topic].result(timeout=10)
    marker=uuid.uuid4().hex
    producer=Producer({'bootstrap.servers':brokers,'enable.idempotence':True,'acks':'all','message.timeout.ms':10000})
    delivered=[]
    def callback(error,msg):
        if error:raise RuntimeError(str(error))
        delivered.append(msg.offset())
        print('PRODUCED topic=',msg.topic(),'partition=',msg.partition(),'offset=',msg.offset())
    producer.produce(topic,key=marker,value=json.dumps({'id':marker,'text':'Hello Kafka, 第十四周作业！'},ensure_ascii=False).encode(),on_delivery=callback)
    if producer.flush(15) or not delivered:raise RuntimeError('Kafka 未确认消息')
    consumer=Consumer({'bootstrap.servers':brokers,'group.id':'wxw14-demo-'+marker,'auto.offset.reset':'earliest','enable.auto.commit':False})
    consumer.subscribe([topic]);deadline=time.monotonic()+30
    try:
        while time.monotonic()<deadline:
            msg=consumer.poll(1)
            if msg is None:continue
            if msg.error():raise RuntimeError(str(msg.error()))
            value=json.loads(msg.value())
            if value.get('id')!=marker:continue
            print('CONSUMED',json.dumps(value,ensure_ascii=False))
            consumer.commit(message=msg,asynchronous=False)
            from confluent_kafka import TopicPartition
            committed=consumer.committed([TopicPartition(topic,msg.partition())],timeout=10)[0].offset
            assert committed==msg.offset()+1
            print('COMMITTED next_offset=',committed,'(message offset + 1)')
            break
        else:raise TimeoutError('没有收到本次消息')
    finally:consumer.close()

if __name__=='__main__':main()
