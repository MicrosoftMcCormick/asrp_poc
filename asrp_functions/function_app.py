"""Azure Functions v4 (Python) entrypoint for the ASRP Function App.

Thin router that registers HTTP triggers on the v4 ``FunctionApp`` and
delegates each request to the canonical handler in :mod:`functions`:

* ``generate_slide_description`` →
  :func:`functions.generate_slide_description.main`
* ``assemble_deck`` → :func:`functions.assemble_deck.main`
* ``notify_user`` → :func:`functions.notify_user.main`

Auth model: HTTP routes are ``AuthLevel.FUNCTION`` for Phase 1 — the
Copilot Studio Agent calls these endpoints with a function key. All
outbound Azure SDK calls use Managed Identity via :mod:`shared.auth`.
"""

from __future__ import annotations

import logging

import azure.functions as func

try:
    from functions.assemble_deck import main as assemble_deck_main
    from functions.generate_slide_description import (
        main as generate_slide_description_main,
    )
    from functions.notify_user import main as notify_user_main
except Exception:
    logging.exception("FUNCTION_APP_IMPORT_FAILURE")
    raise


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.function_name(name="generate_slide_description")
@app.route(route="slides/generate", methods=["POST"])
def generate_slide_description(req: func.HttpRequest) -> func.HttpResponse:
    return generate_slide_description_main(req)


@app.function_name(name="assemble_deck")
@app.route(route="deck/assemble", methods=["POST"])
def assemble_deck(req: func.HttpRequest) -> func.HttpResponse:
    return assemble_deck_main(req)


@app.function_name(name="notify_user")
@app.route(route="notify", methods=["POST"])
def notify_user(req: func.HttpRequest) -> func.HttpResponse:
    return notify_user_main(req)
