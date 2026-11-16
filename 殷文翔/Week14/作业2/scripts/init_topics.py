"""Create only the two configured project topics; never delete or alter topics."""
import argparse
import os
from pathlib import Path
import sys

from kafka import KafkaAdminClient
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partitions', type=int, default=int(os.environ.get('KAFKA_TOPIC_PARTITIONS', '3')))
    parser.add_argument('--replication-factor', type=int,
                        default=int(os.environ.get('KAFKA_TOPIC_REPLICATION_FACTOR', '1')))
    args = parser.parse_args()
    if args.partitions <= 0 or args.replication_factor <= 0:
        parser.error('Partitions and replication factor must be positive')
    settings = get_settings()
    topics = [settings.kafka_pdf_topic, settings.kafka_failed_topic]
    if len(set(topics)) != 2:
        parser.error('Parse and failed topics must differ')
    admin = KafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers,
                             request_timeout_ms=settings.kafka_publish_timeout_seconds * 1000)
    try:
        for topic in topics:
            try:
                admin.create_topics([NewTopic(topic, args.partitions, args.replication_factor)])
            except TopicAlreadyExistsError:
                pass
        for topic in admin.describe_topics(topics):
            print(topic['name'], 'partitions:', len(topic['partitions']))
    finally:
        admin.close()
