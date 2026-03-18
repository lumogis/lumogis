"""HTTP routes for the example plugin."""

from fastapi import APIRouter

STATS: dict[str, int] = {"documents_ingested": 0}

router = APIRouter(prefix="/example", tags=["example"])


@router.get("/stats")
def example_stats():
    """Return counters updated by the DOCUMENT_INGESTED hook."""
    return {"plugin": "example", **STATS}
