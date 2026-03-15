from typing import Protocol


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[dict], limit: int) -> list[dict]: ...
