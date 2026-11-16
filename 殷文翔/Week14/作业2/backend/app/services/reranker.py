"""Bounded multimodal scoring; invalid/partial responses never alter recall order."""
import logging
import math
import time

import httpx

logger = logging.getLogger(__name__)
DEGRADED = '重排服务暂不可用，已降级为原始检索排序；本次相关性未经过重排验证。'


class RerankError(Exception):
    pass


class RerankerClient:
    def __init__(self, settings):
        self.settings = settings

    def score(self, query, documents, images=None):
        if len(images or []) > self.settings.rerank_max_images or len(query) > 12000:
            raise RerankError('query_budget')
        query_input = {'content': [{'type': 'text', 'text': query}, *(images or [])]}
        deadline = time.monotonic() + self.settings.rerank_timeout_seconds
        scores = []
        try:
            with httpx.Client(trust_env=False) as client:
                for start in range(0, len(documents), self.settings.rerank_batch_size):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RerankError('timeout')
                    batch = documents[start:start + self.settings.rerank_batch_size]
                    response = client.post(self.settings.rerank_base_url.rstrip('/') + '/score',
                        headers={'Authorization': 'Bearer ' + self.settings.rerank_api_key.get_secret_value()},
                        timeout=remaining, json={'model': self.settings.rerank_model,
                            'queries': query_input, 'documents': batch,
                            'instruction': 'Retrieve images or text relevant to the user query.'})
                    response.raise_for_status()
                    body = response.json()
                    data = body['data']
                    if body.get('model') != self.settings.rerank_model or len(data) != len(batch):
                        raise ValueError('model/count')
                    by_index = {}
                    for item in data:
                        index, score = item['index'], item['score']
                        if (type(index) is not int or index not in range(len(batch)) or index in by_index
                                or type(score) not in (int, float) or not math.isfinite(score)
                                or not 0 <= score <= 1):
                            raise ValueError('invalid score/index')
                        by_index[index] = score
                    scores.extend(by_index[i] for i in range(len(batch)))
                if time.monotonic() > deadline:
                    raise RerankError('timeout')
            return scores
        except httpx.TimeoutException:
            raise RerankError('timeout') from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            raise RerankError('unavailable_or_invalid_response') from None


def rerank(settings, query, hits, documents, images=None):
    try:
        scores = RerankerClient(settings).score(query, documents, images)
    except RerankError as exc:
        # No response bodies, document content, credentials or data URLs in logs.
        logger.warning('rerank_degraded', extra={'reason': str(exc), 'candidate_count': len(hits)})
        return [dict(hit, rerank_status='degraded') for hit in hits], DEGRADED
    ranked = [dict(hit, retrieval_score=hit.get('score', 0), score=score,
                   rerank_score=score, rerank_status='completed') for hit, score in zip(hits, scores)]
    return sorted(ranked, key=lambda hit: -hit['rerank_score']), None
