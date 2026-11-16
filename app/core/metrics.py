import json
import logging
import threading
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

class MetricsCollector:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MetricsCollector, cls).__new__(cls)
                cls._instance._counters = {}
                cls._instance._lock = threading.Lock()
        return cls._instance

    def increment(self, name: str, labels: Dict[str, str] = None, amount: int = 1):
        with self._lock:
            key = self._format_key(name, labels)
            self._counters[key] = self._counters.get(key, 0) + amount

    def _format_key(self, name: str, labels: Dict[str, str] = None) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def get_all(self) -> Dict[str, int]:
        with self._lock:
            return self._counters.copy()

    def reset(self):
        with self._lock:
            self._counters.clear()

    def report_json(self) -> str:
        return json.dumps({
            "timestamp": datetime.now().isoformat(),
            "metrics": self.get_all()
        })

metrics = MetricsCollector()

def track_groq(status: str):
    metrics.increment("groq_calls_total", {"status": status})

def track_sheets(operation: str, sheet: str = "unknown"):
    metrics.increment("sheets_api_calls_total", {"op": operation, "sheet": sheet})

def track_fonnte(status: str):
    metrics.increment("fonnte_calls_total", {"status": status})

def track_log(status: str):
    metrics.increment("log_events_total", {"status": status})
