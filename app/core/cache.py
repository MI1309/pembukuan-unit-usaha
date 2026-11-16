import time
from collections import OrderedDict
from typing import Any, Optional, Callable

class TTLCache:
    def __init__(self, maxsize: int = 100, default_ttl: int = 300):
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self.cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        if key not in self.cache:
            return None
        
        value, expiry = self.cache[key]
        if time.time() > expiry:
            del self.cache[key]
            return None
            
        # Move to end (LRU)
        self.cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if key in self.cache:
            del self.cache[key]
        
        if len(self.cache) >= self.maxsize:
            self.cache.popitem(last=False)
            
        expiry = time.time() + (ttl if ttl is not None else self.default_ttl)
        self.cache[key] = (value, expiry)

    def clear(self) -> None:
        self.cache.clear()

    def invalidate(self, key: str) -> None:
        if key in self.cache:
            del self.cache[key]

# Global cache instances
groq_cache = TTLCache(maxsize=100, default_ttl=300)  # 5 minutes
master_data_cache = TTLCache(maxsize=50, default_ttl=300)  # 5 minutes
meta_cache = TTLCache(maxsize=20, default_ttl=10)  # 10 seconds
