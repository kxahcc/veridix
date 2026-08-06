from __future__ import annotations


PROVIDER_PRESETS = [
    {
        "id": "openai",
        "name": "OpenAI",
        "endpoint": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
        "embedding_models": ["text-embedding-3-small", "text-embedding-3-large"],
        "requires_api_key": True,
        "local": False,
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "endpoint": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"],
        "embedding_models": [],
        "requires_api_key": True,
        "local": False,
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "endpoint": "https://api.anthropic.com/v1",
        "models": ["claude-3-5-sonnet-latest", "claude-3-7-sonnet-latest"],
        "embedding_models": [],
        "requires_api_key": True,
        "local": False,
    },
    {
        "id": "ollama",
        "name": "Ollama（本地）",
        "endpoint": "http://127.0.0.1:11434/v1",
        "models": ["llama3.1", "qwen2.5", "deepseek-r1", "qwen3"],
        "embedding_models": ["nomic-embed-text", "bge-m3"],
        "requires_api_key": False,
        "local": True,
    },
    {
        "id": "local",
        "name": "本地兼容端点",
        "endpoint": "http://127.0.0.1:8000/v1",
        "models": ["local-model"],
        "embedding_models": ["local-embedding"],
        "requires_api_key": False,
        "local": True,
    },
    {
        "id": "azure-openai",
        "name": "Azure OpenAI",
        "endpoint": "https://<resource>.openai.azure.com/openai/v1",
        "models": ["gpt-4o"],
        "embedding_models": ["text-embedding-3-small"],
        "requires_api_key": True,
        "local": False,
    },
]


def list_provider_presets() -> list[dict]:
    return [dict(item) for item in PROVIDER_PRESETS]
