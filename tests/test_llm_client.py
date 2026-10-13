"""模型客户端测试：用 httpx.MockTransport 模拟网关，不发真实请求。

流式样例照着学校网关（vLLM）的真实返回格式写：思考在 delta.reasoning，
工具参数分多块到达，最后一个 chunk 只带 usage。
"""

import json

import httpx
import pytest

from margin import worker
from margin.assembly import build_llm
from margin.llm import LLMTimeout, OpenAICompatibleClient
from margin.settings import Settings

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


def two_replica_settings() -> Settings:
    return Settings(_env_file=None, llm_base_url="http://gateway/v1", blocks_path="b",
                    manifest_path="m", alias_path="a", database_url="postgresql://x",
                    local_models={"C2": ["http://gpu0/v1", "http://gpu1/v1"]})


def test_self_trained_model_spreads_over_replicas_with_training_sampling():
    """自训模型：两个副本分到两张卡；请求按训练时的参数发（温度 0.6、开思考、单次 5000 token），
    不然测到的不是训练出来的行为。不在表里的模型照旧走网关，不带这些参数。"""
    settings = two_replica_settings()
    sent = []

    def capture(client: OpenAICompatibleClient) -> OpenAICompatibleClient:
        client.http = httpx.Client(base_url=client.http.base_url, transport=httpx.MockTransport(
            lambda request: sent.append((str(request.url), json.loads(request.content)))
            or httpx.Response(200, json={"choices": [{"message": {}, "finish_reason": "stop"}]})))
        return client

    capture(build_llm(settings, "C2", replica=0)).chat(MESSAGES, TOOLS, 8192, "auto")
    capture(build_llm(settings, "C2", replica=3)).complete(MESSAGES, 2000)
    capture(build_llm(settings, "DeepSeek")).chat(MESSAGES, TOOLS, 8192, "auto")

    assert [url for url, _ in sent] == ["http://gpu0/v1/chat/completions",
                                        "http://gpu1/v1/chat/completions",
                                        "http://gateway/v1/chat/completions"]
    for _, body in sent[:2]:
        assert body["temperature"] == 0.6
        assert body["max_tokens"] == 5000
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert "temperature" not in sent[2][1] and sent[2][1]["max_tokens"] == 8192


def test_worker_keeps_one_client_per_replica(monkeypatch):
    """每道题的 attempt_id 都不同。直接拿它当缓存键，每道题都会新建一个客户端（连同连接池）
    且不释放；应该是网关模型全程一个客户端，自训模型每个副本一个，按序号轮流。"""
    monkeypatch.setattr(worker, "get_settings", two_replica_settings)
    worker._llm.cache_clear()
    try:
        assert worker._llm_for("DeepSeek", 11) is worker._llm_for("DeepSeek", 12)
        assert worker._llm_for("C2", 11) is worker._llm_for("C2", 13)
        assert worker._llm_for("C2", 11) is not worker._llm_for("C2", 12)
        assert worker._llm.cache_info().currsize == 3
    finally:
        worker._llm.cache_clear()
