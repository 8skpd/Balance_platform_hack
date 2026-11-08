import logging
import time
import redis
from . import store
from .queueing import dispatch_pending
from .settings import REDIS_URL, QUEUE_NAME

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    store.init()
    client = redis.Redis.from_url(REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    while True:
        try:
            dispatch_pending()
            client.set(f'{QUEUE_NAME}:scheduler', 'ready', ex=10)
        except Exception:
            logging.exception('Планировщик повторит операцию')
        time.sleep(2)
