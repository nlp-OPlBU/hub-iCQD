from kafka import KafkaProducer

producer = KafkaProducer(bootstrap_servers="localhost:9092")

while True:
    msg = input("输入消息（exit 退出）: ")
    if msg.strip().lower() == "exit":
        break
    producer.send("demo-topic", msg.encode("utf-8"))
    producer.flush()
    print("发送:", msg)

producer.close()
