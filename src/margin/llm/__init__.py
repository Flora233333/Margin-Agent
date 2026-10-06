"""模型接入层。"""

from .client import (
    ChatModel,
    LLMResponse,
    LLMTimeout,
    OnDelta,
    OpenAICompatibleClient,
    describe_error,
)

__all__ = ["ChatModel", "LLMResponse", "LLMTimeout", "OnDelta", "OpenAICompatibleClient",
           "describe_error"]
