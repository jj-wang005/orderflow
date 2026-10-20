from fastapi import APIRouter
from app.schemas.api import Envelope
from app.schemas.assistant import ChatRequest
from app.services.assistant import chat

router = APIRouter(prefix="/api/v1/assistant", tags=["C豆智能助手"])

@router.post("/chat")
def answer(body: ChatRequest):
    return Envelope(data={"reply": chat(body)})
