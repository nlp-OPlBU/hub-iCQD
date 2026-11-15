from app.worker.indexing import embed, split_chunks, tokenize
from app.worker.kafka_consumer import decode_task_uid


def test_markdown_chunks_keep_heading_context():
    result = split_chunks("# Main\n\nparagraph one\n\n## Child\n\nparagraph two")
    assert [chunk["heading"] for chunk in result] == ["Main", "Child"]
    assert result[1]["heading_path"] == "Main > Child"


def test_local_embeddings_are_normalized_and_repeatable():
    vectors = embed(["多模态检索"])
    assert len(vectors[0]) == 512
    assert abs(sum(v * v for v in vectors[0]) ** 0.5 - 1.0) < 1e-6
    assert vectors == embed(["多模态检索"])


def test_tokenizer_returns_terms():
    assert tokenize("RAG 检索")


def test_kafka_event_validation():
    import json
    valid=json.dumps({"schema_version":1,"event_type":"document.parse.requested","task_uid":"task_1"}).encode()
    assert decode_task_uid(valid)=="task_1"
    assert decode_task_uid(b"not-json") is None
    assert decode_task_uid(json.dumps({"schema_version":2,"task_uid":"task_1"}).encode()) is None
