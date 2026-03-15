"""Thin wrapper around the Anthropic SDK pointed at the LiteLLM proxy."""

import os

import httpx
from anthropic import Anthropic


def get_client() -> Anthropic:
    url = os.environ.get("LITELLM_URL", "http://litellm:4000")
    return Anthropic(
        api_key="ignored",
        base_url=url,
        timeout=120.0,
        http_client=httpx.Client(timeout=120.0),
    )
