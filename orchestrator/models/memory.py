from pydantic import BaseModel


class SessionSummary(BaseModel):
    session_id: str
    summary: str
    topics: list[str] = []
    entities: list[str] = []


class ContextHit(BaseModel):
    session_id: str
    summary: str
    score: float
