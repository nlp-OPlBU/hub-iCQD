FROM python:3.12-slim
WORKDIR /practice
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple redis==7.4.1 confluent-kafka==2.16.1 python-dotenv==1.2.4
COPY redis_demo.py kafka_demo.py ./
CMD ["python", "redis_demo.py"]
