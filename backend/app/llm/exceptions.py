class LLMError(Exception):
    """Base class for all LLM integration errors."""


class LLMAuthenticationError(LLMError):
    """API key missing or rejected by the provider. Not retryable."""


class LLMBadRequestError(LLMError):
    """Request rejected as invalid (bad schema, unsupported param, etc). Not retryable."""


class LLMRateLimitError(LLMError):
    """Rate limited by the provider. Raised only after retries are exhausted."""


class LLMTimeoutError(LLMError):
    """Request timed out. Raised only after retries are exhausted."""


class LLMServerError(LLMError):
    """Provider-side failure (5xx) or connection error. Raised only after retries are exhausted."""
