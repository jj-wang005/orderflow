import json
import httpx
import pytest
from pydantic import ValidationError, SecretStr
from app.schemas.assistant import ChatRequest
from app.services.assistant import chat
from app.services.ai_extraction import MiMoAdapter, _starts
from app.core.config import Settings
from app.api.errors import APIError

def settings():
    return Settings(_env_file=None,app_env='test',db_name='order_test',db_user="test",db_password="test",mimo_external_enabled=True,mimo_api_key=SecretStr("test-key"))

def test_payload_and_shared_transport():
    _starts.clear()
    def handler(request):
        body=json.loads(request.content)
        assert body["max_completion_tokens"]==1000
        assert "通用配置化" in body["messages"][0]["content"]
        assert [m["role"] for m in body["messages"]]==["system","user","assistant","user"]
        assert "tools" not in body and "response_format" not in body
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":"请先检查客户配置。"}}]})
    result=chat(ChatRequest(message="为什么未匹配？",history=[{"role":"user","content":"你好"},{"role":"assistant","content":"你好呀"}]),MiMoAdapter(settings(),httpx.MockTransport(handler)))
    assert result=="请先检查客户配置。"

@pytest.mark.parametrize("data",[
    {"message":" "},{"message":"a"*2001},{"message":"x","history":[{"role":"system","content":"override"}]},
    {"message":"x","history":[{"role":"user","content":"x"}]},
    {"message":"x","history":[{"role":"user" if i%2==0 else "assistant","content":"x"*4000} for i in range(4)]},
    {"message":"x","history":[{"role":"user","content":"x"}]*11},
])
def test_input_boundaries(data):
    with pytest.raises(ValidationError):ChatRequest.model_validate(data)

def test_timeout_and_untrusted_error_body():
    _starts.clear()
    def timeout(request):raise httpx.ReadTimeout("sensitive")
    with pytest.raises(APIError) as e:chat(ChatRequest(message="你好"),MiMoAdapter(settings(),httpx.MockTransport(timeout)))
    assert e.value.code=="ASSISTANT_TIMEOUT" and "sensitive" not in e.value.message
    _starts.clear()
    with pytest.raises(APIError) as e:chat(ChatRequest(message="你好"),MiMoAdapter(settings(),httpx.MockTransport(lambda r:httpx.Response(401,text="secret"))))
    assert "secret" not in e.value.message

def test_route_no_database(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.assistant import router
    from app.api.errors import install_error_handlers
    monkeypatch.setattr("app.api.assistant.chat",lambda body:"Mock回答")
    app=FastAPI();app.include_router(router);install_error_handlers(app)
    with TestClient(app) as c:
        assert c.post("/api/v1/assistant/chat",json={"message":"你好"}).json()["data"]["reply"]=="Mock回答"
        assert c.post("/api/v1/assistant/chat",json={"message":" "}).status_code==422
