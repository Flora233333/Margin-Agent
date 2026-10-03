"""模型接入层。"""

from .client import ChatModel, LLMResponse, OnDelta, OpenAICompatibleClient

__all__ = ["ChatModel", "LLMResponse", "OnDelta", "OpenAICompatibleClient"]
