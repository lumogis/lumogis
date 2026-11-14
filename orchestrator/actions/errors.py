# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Action-execution error types.

``PermanentActionError`` marks a handler failure that cannot succeed on a retry —
stale UIDs, unsupported or absent mailbox capabilities, and similar (LUM-623). The
executor catches it and flags the resulting ``ActionResult`` as ``permanent``.

**Since LUM-762 that flag no longer selects a disposal.** Every failure of a
claimed proposal is terminal on the first attempt; there is no backoff and no
``executing → approved`` edge for a permanent error to be spared from. What
``permanent`` still does is select the error **code** the web composer renders, so
a user sees "the mailbox has no Drafts folder" rather than a generic failure. Keep
raising it for the precision, not for the routing.
"""

from __future__ import annotations


class PermanentActionError(Exception):
    """A handler failure that is permanent — retrying cannot help.

    ``code`` is a short machine token (e.g. ``"stale"``, ``"unsupported_move"``,
    ``"needs_folder_move"``, ``"no_message_id"``) surfaced in the error string.
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)
