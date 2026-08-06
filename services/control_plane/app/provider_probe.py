from __future__ import annotations

from typing import Any

import httpx


def probe_provider(
    profile: dict[str, Any],
    *,
    resolver=None,
) -> dict[str, Any]:
    endpoint = profile["endpoint"].rstrip("/")
    timeout = float(profile.get("timeout_seconds", 5))
    headers: dict[str, str] = {}
    api_key = (
        resolver.resolve(profile.get("api_key_ref"))
        if resolver is not None
        else _resolve_secret(profile.get("api_key_ref"))
    )
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        models = _probe_models_best_effort(endpoint, headers, timeout)
        if profile.get("backend") == "litellm":
            chat_ok, chat_detail = _probe_litellm_chat(
                profile.get("model", ""),
                api_key,
                endpoint,
                timeout,
            )
            if not chat_ok:
                return {
                    "status": "degraded",
                    "reason": "rag_degraded:provider_unavailable",
                    "event_type": "rag_degraded",
                    "detail": chat_detail,
                }
            return {
                "status": "ok",
                "capabilities": {
                    "models": models,
                    "chat": True,
                    "embeddings": False,
                    "dimensions": None,
                    "rerank": False,
                },
                "event_type": None,
            }
        chat_ok, chat_detail = _probe_chat(
            endpoint,
            profile.get("model", ""),
            headers,
            timeout,
        )
        if not chat_ok:
            return {
                "status": "degraded",
                "reason": "rag_degraded:provider_unavailable",
                "event_type": "rag_degraded",
                "detail": chat_detail,
            }
        embedding_ok, dimensions = _probe_embeddings_optional(
            endpoint,
            profile.get("model", ""),
            headers,
            timeout,
        )
        rerank_ok = _probe_rerank_optional(
            endpoint,
            profile.get("model", ""),
            headers,
            timeout,
        )
        if not embedding_ok:
            return {
                "status": "ok",
                "capabilities": {
                    "models": models,
                    "chat": True,
                    "embeddings": False,
                    "dimensions": None,
                    "rerank": rerank_ok,
                },
                "event_type": None,
                "detail": "embeddings endpoint unavailable (chat provider only)",
            }
        return {
            "status": "ok",
            "capabilities": {
                "models": models,
                "chat": True,
                "embeddings": embedding_ok,
                "dimensions": dimensions,
                "rerank": rerank_ok,
            },
            "event_type": None,
        }
    except Exception as error:
        return {
            "status": "degraded",
            "reason": f"rag_degraded:{_classify(error)}",
            "event_type": "rag_degraded",
            "detail": str(error),
        }


def list_provider_models(
    profile: dict[str, Any],
    *,
    resolver=None,
) -> list[str]:
    endpoint = profile["endpoint"].rstrip("/")
    timeout = float(profile.get("timeout_seconds", 5))
    headers: dict[str, str] = {}
    api_key = (
        resolver.resolve(profile.get("api_key_ref"))
        if resolver is not None
        else _resolve_secret(profile.get("api_key_ref"))
    )
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    models = _probe_models_best_effort(endpoint, headers, timeout)
    if profile.get("backend") == "litellm" and not models:
        model = str(profile.get("model") or "")
        return [model] if model else []
    if not models:
        model = str(profile.get("model") or "")
        return [model] if model else []
    return models


def _probe_litellm_chat(
    model: str,
    api_key: str | None,
    endpoint: str,
    timeout: float,
) -> tuple[bool, str]:
    try:
        import litellm

        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            api_key=api_key,
            api_base=endpoint,
            timeout=timeout,
        )
        return bool(response.choices), "ok"
    except Exception as error:
        return False, str(error)


def _probe_models_best_effort(
    endpoint: str,
    headers: dict[str, str],
    timeout: float,
) -> list[str]:
    for path in ("/models", "/v1/models"):
        try:
            response = httpx.get(
                f"{endpoint}{path}",
                headers=headers,
                timeout=timeout,
                trust_env=False,
            )
            if response.status_code != 200:
                continue
            data = response.json().get("data", [])
            models = sorted(item["id"] for item in data if item.get("id"))
            if models:
                return models
        except Exception:
            continue
    return []


def _probe_chat(
    endpoint: str,
    model: str,
    headers: dict[str, str],
    timeout: float,
) -> tuple[bool, str]:
    for path in ("/chat/completions", "/v1/chat/completions"):
        try:
            response = httpx.post(
                f"{endpoint}{path}",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                timeout=timeout,
                trust_env=False,
            )
            if response.status_code == 200:
                return True, "ok"
            if response.status_code != 404:
                return False, response.text[:200]
        except Exception as error:
            return False, str(error)
    return False, "chat completions endpoint unavailable"


def _probe_embeddings_optional(
    endpoint: str,
    model: str,
    headers: dict[str, str],
    timeout: float,
) -> tuple[bool, int | None]:
    try:
        response = httpx.post(
            f"{endpoint}/embeddings",
            headers={**headers, "Content-Type": "application/json"},
            json={"model": model, "input": ["probe"]},
            timeout=timeout,
            trust_env=False,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        if not data or not data[0].get("embedding"):
            return False, None
        return True, len(data[0]["embedding"])
    except Exception:
        return False, None


def _probe_rerank_optional(
    endpoint: str,
    model: str,
    headers: dict[str, str],
    timeout: float,
) -> bool:
    try:
        response = httpx.post(
            f"{endpoint}/rerank",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "model": model,
                "query": "probe",
                "documents": ["first", "second"],
            },
            timeout=timeout,
            trust_env=False,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return len(results) >= 2
    except Exception:
        return False


def _resolve_secret(ref: str | None) -> str | None:
    if not ref:
        return None
    scheme, _, name = ref.partition(":")
    if scheme == "env" and name:
        import os

        return os.environ.get(name)
    return None


def _classify(error: Exception) -> str:
    name = type(error).__name__.lower()
    if "timeout" in name:
        return "provider_timeout"
    if "connect" in name:
        return "provider_unavailable"
    if "httpstatus" in name:
        return "provider_unavailable"
    return "provider_unavailable"
