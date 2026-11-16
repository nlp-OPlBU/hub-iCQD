"""Qwen multimodal embeddings: real image content, bounded input, strict vectors."""
import base64
import io
import math
import httpx
from app.services.elasticsearch_store import ElasticsearchError
from app.services.images import decode_image, resource_path


class EmbeddingClient:
    def __init__(self, settings):
        self.settings = settings

    def image_part(self, key):
        path = resource_path(self.settings, key)
        if path.stat().st_size > self.settings.image_max_file_bytes:
            raise ElasticsearchError('Embedding image exceeds byte limit')
        image, _, _ = decode_image(path.read_bytes(), self.settings)
        try:
            image.thumbnail((768, 768))
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=85)
            return {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' +
                    base64.b64encode(output.getvalue()).decode()}}
        finally:
            image.close()

    def encode(self, text, images=None):
        # Server permits one image per input. Multi-image inputs are represented by
        # normalized mean of independently encoded text+image pairs, on both sides.
        vectors = [self._one(text, image) for image in (images or [None])]
        vector = [sum(values) / len(vectors) for values in zip(*vectors)]
        norm = math.sqrt(sum(x*x for x in vector))
        if not math.isfinite(norm) or norm < 1e-12:
            raise ElasticsearchError('Invalid embedding norm')
        return [x / norm for x in vector]

    def _one(self, text, image):
        content = ([image] if image else []) + ([{'type': 'text', 'text': text}] if text else [])
        if not content or len(text) > 12000:
            raise ElasticsearchError('Empty or oversized embedding input')
        payload = {'model': self.settings.embedding_model, 'encoding_format': 'float',
            'messages': [{'role': 'system', 'content': "Represent the user's input."},
                         {'role': 'user', 'content': content}, {'role': 'assistant', 'content': ''}],
            'add_generation_prompt': False, 'continue_final_message': True, 'add_special_tokens': False}
        try:
            with httpx.Client(timeout=self.settings.embedding_timeout_seconds, trust_env=False) as client:
                response = client.post(self.settings.embedding_base_url.rstrip('/') + '/embeddings',
                    headers={'Authorization': 'Bearer ' + self.settings.embedding_api_key.get_secret_value()}, json=payload)
                response.raise_for_status()
                data = response.json()
            vector = data['data'][0]['embedding']
            if len(data['data']) != 1 or len(vector) != self.settings.embedding_dimensions:
                raise ValueError('dimension mismatch')
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
                raise ValueError('nonfinite vector')
            return vector
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise ElasticsearchError('Embedding service failed or returned invalid vector') from exc
