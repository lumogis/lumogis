"""Data endpoints: /ingest, /search, /session/end, /entities/extract."""

from fastapi import APIRouter
from fastapi import BackgroundTasks
from pydantic import BaseModel
from services.ingest import ingest_folder
from services.search import semantic_search

router = APIRouter()


class IngestRequest(BaseModel):
    path: str = "/data"


@router.post("/ingest")
def ingest_endpoint(body: IngestRequest, bg: BackgroundTasks):
    bg.add_task(ingest_folder, body.path)
    return {"status": "ingest started", "path": body.path}


@router.get("/search")
def search_endpoint(q: str, limit: int = 5):
    results = semantic_search(q, limit=limit)
    return [r.model_dump() for r in results]
