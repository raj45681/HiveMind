"""Free model choices and bounded OpenRouter error handling; no network on setup."""
from email.utils import parsedate_to_datetime
import math
import random
import re
import time


# Catalog checked 2026-10-19: all five named models advertise structured_outputs
# and zero prompt/completion prices. The router filters for required parameters.
FREE_MODELS = (
    ('openrouter/free', 'Automatic free router'),
    ('nvidia/nemotron-3-super-120b-a12b:free', 'NVIDIA Nemotron 3 Super'),
    ('xingchen-agi/xing4.0-29b-a4b:free', 'Xing4.0 29B A4B'),
    ('dots-studio/dots-3-note-preview:free', 'Dots3-Note Preview'),
    ('nvidia/nemotron-3.5-super-vl:free', 'NVIDIA Nemotron 3.5 Super VL'),
    ('liquid/lfm-2.5-2.6b:free', 'LiquidAI LFM2.5-2.6B'),
)
MAX_ATTEMPTS = 3  # Initial request plus two retries, each reserved in the ledger.


def is_free_model(model):
    return isinstance(model, str) and (model == 'openrouter/free' or bool(
        re.fullmatch(r'[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.:-]+:free', model)))


def model_pool(model, free_only, fallbacks=None):
    if not free_only:
        return [model]  # Cheap mode retains only its explicitly selected model.
    if fallbacks is None:
        fallbacks = [ident for ident, _ in FREE_MODELS]
    if not isinstance(fallbacks, (list, tuple)) or len(fallbacks) > 6:
        raise ValueError('fallback_models must be a list of at most six free model IDs')
    if not all(is_free_model(ident) for ident in [model, *fallbacks]):
        raise ValueError('Free fallback models must use openrouter/free or an explicit :free ID')
    return list(dict.fromkeys([model, *fallbacks]))


def retry_after(value):
    """Read standard delta seconds or HTTP dates without retaining provider text."""
    if not isinstance(value, str) or len(value) > 200:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            delay = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, delay) if math.isfinite(delay) else None


def retry_delay(attempt, requested=None):
    # Jitter avoids synchronized clients. Long server delays stay on disk, not asleep.
    backoff = min(60, 5 * 2 ** max(0, attempt - 1)) + random.uniform(0, 1)
    return max(backoff, requested or 0)


class OpenRouterFailure(RuntimeError):
    def __init__(self, code, delay=None, usage=None):
        super().__init__('OpenRouter HTTP ' + str(code))
        self.code = code
        self.retry_after = delay
        self.usage = usage or {}
        self.retryable = code in {408, 429, 500, 502, 503, 504} or (code == 402 and delay is not None)
