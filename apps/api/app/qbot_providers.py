"""Ordered model fallback for Q仔's free-form answers."""
import logging
import os
from time import monotonic

import httpx

logger = logging.getLogger(__name__)


class ProviderUnavailable(Exception):
    pass


def configured_providers(allow_fallback):
    providers = [
        ("DeepSeek", os.getenv("DEEPSEEK_API_KEY"),
         "https://api.deepseek.com/chat/completions",
         os.getenv("DEEPSEEK_MODEL") or "deepseek-flash",
         {"max_tokens": 450, "thinking": {"type": "disabled"}}),
    ]
    if allow_fallback:
        providers += [
            ("Gemini", os.getenv("QBOT_GEMINI_API_KEY"),
             "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
             "gemini-3.1-flash-lite", {"max_tokens": 450, "reasoning_effort": "minimal"}),
            ("OpenAI", os.getenv("QBOT_OPENAI_API_KEY"),
             "https://api.openai.com/v1/chat/completions",
             "gpt-6-luna", {"max_completion_tokens": 450, "reasoning_effort": "none", "store": False}),
        ]
    return [provider for provider in providers if provider[1]]


def answer(providers, instructions, content):
    messages = [{"role": "system", "content": instructions},
                {"role": "user", "content": content}]
    # The trial gateway waits 29 seconds. Keep the attempts within its budget.
    deadline = monotonic() + 24
    for index, (name, key, url, model, options) in enumerate(providers):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        timeout = min(remaining, 7 if index < len(providers) - 1 else 24)
        try:
            response = httpx.post(url, headers={"Authorization": f"Bearer {key}"},
                                  json={"model": model, "messages": messages, **options},
                                  timeout=httpx.Timeout(timeout, connect=min(timeout, 3)))
            response.raise_for_status()
            choice = response.json()["choices"][0]
            message = choice["message"]
            # Do not resubmit a request another provider has declined on content grounds.
            if choice.get("finish_reason") == "content_filter" or message.get("refusal"):
                raise ProviderUnavailable
            reply = message.get("content")
            if not isinstance(reply, str) or not reply.strip() or choice.get("finish_reason") == "length":
                raise ValueError("incomplete answer")
            if index:
                logger.info("Qbot fallback answered via %s", name)
            return reply.strip()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            logger.warning("Qbot provider %s returned HTTP %d", name, status)
            if status not in (401, 402, 403, 404, 408, 429) and status < 500:
                break
        except (httpx.RequestError, KeyError, IndexError, ValueError, TypeError, AttributeError) as error:
            logger.warning("Qbot provider %s failed: %s", name, type(error).__name__)
    raise ProviderUnavailable
