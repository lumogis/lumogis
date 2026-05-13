"""Example plugin template — copy this folder to orchestrator/plugins/example/.

See README.md in this directory.
"""

import logging

from events import Event
from hooks import register

from . import routes

_log = logging.getLogger(__name__)


def _on_document_ingested(file_path: str, chunk_count: int, **kwargs) -> None:
    routes.STATS["documents_ingested"] += 1
    _log.info(
        "[example_plugin] DOCUMENT_INGESTED: %s (%d chunks)",
        file_path,
        chunk_count,
    )


register(Event.DOCUMENT_INGESTED, _on_document_ingested)

# Plugin loader looks for this name:
router = routes.router
