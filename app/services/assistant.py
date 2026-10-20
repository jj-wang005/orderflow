from pathlib import Path
from app.api.errors import APIError
from app.core.config import get_settings
from app.services.ai_extraction import MiMoAdapter, MODEL

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"

def chat(body, adapter=None):
    if adapter is None:
        settings = get_settings()
        if settings.ai_provider != "mimo":
            raise APIError(503, "ASSISTANT_NOT_ENABLED", "C豆问答尚未启用 MiMo，仍可查看操作指南。")
        adapter = MiMoAdapter(settings)
    system = (KNOWLEDGE / "assistant_system.md").read_text(encoding="utf-8")
    guide = (KNOWLEDGE / "business_guide.md").read_text(encoding="utf-8")
    payload = {"model": MODEL, "messages": [{"role":"system", "content":system+"\n受控业务知识：\n"+guide}]
               + [m.model_dump() for m in body.history] + [{"role":"user", "content":body.message}],
               "max_completion_tokens": 1000, "thinking":{"type":"disabled"}, "stream":False}
    try:
        reply, _ = adapter.complete(payload)
    except APIError as error:
        if error.code == "AI_TIMEOUT":
            raise APIError(504, "ASSISTANT_TIMEOUT", "C豆回答超时，请稍后重试。") from None
        raise
    if not reply.strip() or len(reply)>4000: raise APIError(502,"ASSISTANT_INVALID_REPLY","C豆暂时没有完整回答，请重试。")
    return reply
