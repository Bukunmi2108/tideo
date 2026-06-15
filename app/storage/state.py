import redis.asyncio as redis
from app.core.config import config

_client = redis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)

def get_client() -> redis.Redis:
    return _client