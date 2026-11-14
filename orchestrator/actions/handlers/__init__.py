# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
# Concrete action handlers live here.
#
# Implement ActionHandler from ports/action_handler.py and register via
# actions.registry.register_action(). Handler modules are NOT auto-discovered —
# there is no scanning machinery here. A handler becomes active only when it is
# explicitly imported at startup (see main.py, which imports the handler modules
# so their module-level register_action() calls run), mirroring the
# `from services import batch_handlers` precedent.
#
# For distributable handlers, prefer packaging them as a plugin under
# orchestrator/plugins/<name>/ so they can be mounted independently.
# See docs/extending/examples/example_plugin/ for a starting template and
# CONTRIBUTING.md for the full guide.
