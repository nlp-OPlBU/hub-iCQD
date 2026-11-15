"""Authenticated Elasticsearch client factory and health probe."""
from app.config import get_settings


def create_client():
    from elasticsearch import Elasticsearch

    settings = get_settings()
    kwargs = {"hosts": [settings.elasticsearch_url], "request_timeout": 10,
              "verify_certs": settings.elasticsearch_verify_certs}
    if settings.elasticsearch_api_key:
        kwargs["api_key"] = settings.elasticsearch_api_key
    elif settings.elasticsearch_username:
        kwargs["basic_auth"] = (settings.elasticsearch_username, settings.elasticsearch_password)
    return Elasticsearch(**kwargs)


def check_health() -> str:
    try:
        client = create_client()
        return "ok" if client.ping() else "error"
    except Exception:
        return "unavailable"
