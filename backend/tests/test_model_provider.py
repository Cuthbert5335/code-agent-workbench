from __future__ import annotations

import asyncio
from typing import Self

import httpx
import pytest

from app.config import Settings
from app.providers import openai_compatible


class FakeAsyncClient:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.request_kwargs: dict[str, object] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.request_kwargs = {"url": url, **kwargs}
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def provider_settings() -> Settings:
    return Settings(
        model_api_key="secret-key",
        model_base_url="https://provider.example/v1/",
        model_name="compatible-model",
        model_timeout_seconds=7,
    )


def test_request_chat_completion_posts_openai_compatible_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeAsyncClient(
        httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "答案"}},
                ],
            },
        ),
    )
    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", lambda **kwargs: fake_client)

    answer = asyncio.run(
        openai_compatible.request_chat_completion(
            context="用户问题：\n解释代码\n",
            settings=provider_settings(),
        ),
    )

    assert answer == "答案"
    assert fake_client.request_kwargs is not None
    assert fake_client.request_kwargs["url"] == "https://provider.example/v1/chat/completions"
    assert fake_client.request_kwargs["headers"] == {
        "Authorization": "Bearer secret-key",
        "Content-Type": "application/json",
    }
    body = fake_client.request_kwargs["json"]
    assert isinstance(body, dict)
    assert body["model"] == "compatible-model"
    assert body["messages"][0]["content"] == openai_compatible.SYSTEM_PROMPT
    assert body["messages"][1]["content"] == "用户问题：\n解释代码\n"


def test_request_chat_completion_uses_custom_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeAsyncClient(
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": "structured result"}}]},
        ),
    )
    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", lambda **kwargs: fake_client)

    answer = asyncio.run(
        openai_compatible.request_chat_completion(
            context="patch context",
            settings=provider_settings(),
            system_prompt="strict patch prompt",
        ),
    )

    assert answer == "structured result"
    assert fake_client.request_kwargs is not None
    body = fake_client.request_kwargs["json"]
    assert isinstance(body, dict)
    assert body["messages"] == [
        {"role": "system", "content": "strict patch prompt"},
        {"role": "user", "content": "patch context"},
    ]


@pytest.mark.parametrize(
    "provider_exception",
    [httpx.ReadTimeout("timed out"), httpx.ConnectError("offline")],
)
def test_request_chat_completion_maps_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
    provider_exception: Exception,
) -> None:
    fake_client = FakeAsyncClient(provider_exception)
    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", lambda **kwargs: fake_client)

    with pytest.raises(
        openai_compatible.ModelTimeoutError
        if isinstance(provider_exception, httpx.TimeoutException)
        else openai_compatible.ModelProviderError,
    ):
        asyncio.run(
            openai_compatible.request_chat_completion(
                context="context",
                settings=provider_settings(),
            ),
        )


def test_request_chat_completion_rejects_provider_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeAsyncClient(httpx.Response(401, json={"error": {"message": "bad key"}}))
    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", lambda **kwargs: fake_client)

    with pytest.raises(openai_compatible.ModelProviderError):
        asyncio.run(
            openai_compatible.request_chat_completion(
                context="context",
                settings=provider_settings(),
            ),
        )


@pytest.mark.parametrize(
    "payload",
    [{}, {"choices": []}, {"choices": [{"message": {"content": ""}}]}],
)
def test_request_chat_completion_rejects_empty_or_invalid_responses(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    fake_client = FakeAsyncClient(httpx.Response(200, json=payload))
    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", lambda **kwargs: fake_client)

    with pytest.raises(openai_compatible.ModelResponseError):
        asyncio.run(
            openai_compatible.request_chat_completion(
                context="context",
                settings=provider_settings(),
            ),
        )
