"""模型客户端测试：用 httpx.MockTransport 模拟网关，不发真实请求。

流式样例照着学校网关（vLLM）的真实返回格式写：思考在 delta.reasoning，
工具参数分多块到达，最后一个 chunk 只带 usage。
"""

import json

import httpx
import pytest

from margin.llm import LLMTimeout, OpenAICompatibleClient

TOOLS = [{"type": "function", "function": {"name": "search_docs", "parameters": {}}}]
MESSAGES = [{"role": "user", "content": "宁德时代2025年营业收入？"}]


def make_client(handler) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient("http://gateway/v1", "DeepSeek", "test-key")
    client.http = httpx.Client(base_url="http://gateway/v1",
                               transport=httpx.MockTransport(handler))
    return client


def sse(*chunks: dict) -> bytes:
    lines = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def delta(**fields) -> dict:
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def test_non_stream_normalizes_reasoning_field():
    """新版 vLLM 把思考放在 reasoning 字段，统一成 LLMResponse.reasoning。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None, "reasoning": "先查年报",
                                     "tool_calls": []}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}})

    response = make_client(handler).chat(MESSAGES, TOOLS, 512, "auto")

    assert response.reasoning == "先查年报"
    assert response.usage["completion_tokens"] == 3


def test_stream_assembles_reasoning_and_tool_call():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=sse(
            delta(role="assistant", content=""),
            delta(reasoning="先检索"),
            delta(reasoning="年报"),
            delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "search_docs"}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '{"query": "'}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '宁德时代"}'}}]),
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 297, "completion_tokens": 66}},
        ), headers={"content-type": "text/event-stream"})

    deltas = []
    response = make_client(handler).chat(MESSAGES, TOOLS, 512, "auto",
                                         on_delta=lambda *d: deltas.append(d))

    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert deltas == [("reasoning", "先检索"), ("reasoning", "年报")]
    assert response.reasoning == "先检索年报"
    assert response.content is None
    assert response.tool_calls == [{"id": "call_1", "type": "function", "function": {
        "name": "search_docs", "arguments": '{"query": "宁德时代"}'}}]
    assert response.finish_reason == "tool_calls"
    assert response.usage == {"prompt_tokens": 297, "completion_tokens": 66}


def test_stream_that_runs_past_total_timeout_is_aborted():
    """网关一直在推数据、但总时长超了（例如模型反复输出停不下来）：放弃这次调用，而不是一直等。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(delta(reasoning="想")),
                              headers={"content-type": "text/event-stream"})

    client = make_client(handler)
    client.total_timeout = -1  # 截止时间设在过去：收到第一块时就已超时

    with pytest.raises(LLMTimeout):
        client.chat(MESSAGES, TOOLS, 512, "auto", on_delta=lambda *d: None)
