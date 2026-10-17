"""User-private authenticated RentAI Assistant endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.dependencies import CurrentUser, require_roles
from backend.repositories.sqlite_repository import SQLiteRepository
from backend.services.chatbot_service import send_message

router = APIRouter(prefix="/chatbot", tags=["chatbot"])
authenticated_chat_user = Depends(require_roles("owner", "renter", "broker", "admin"))


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    page: str = Field(default="", max_length=100)


@router.get("/messages")
def get_messages(user: CurrentUser = authenticated_chat_user):
    return {"messages": SQLiteRepository().chatbot_history(user.uid)}


@router.post("/messages")
def post_message(payload: ChatRequest, user: CurrentUser = authenticated_chat_user):
    question = payload.message.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Enter a message before sending.")
    try:
        response, mode = send_message(user.uid, user.role, question, payload.page.strip())
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Chat request failed for an authenticated user")
        raise HTTPException(status_code=500, detail="RentAI could not save this conversation. Please try again.") from exc
    return {"answer": response, "mode": mode}


@router.delete("/messages")
def clear_messages(user: CurrentUser = authenticated_chat_user):
    SQLiteRepository().clear_chatbot_history(user.uid)
    return {"cleared": True}
