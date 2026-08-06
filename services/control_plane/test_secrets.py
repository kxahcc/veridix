from __future__ import annotations

from services.control_plane.app.secrets import SecretResolver


def test_secret_resolver_env_and_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VERIDIX_TEST_KEY", "secret-env")
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("secret-file\n", encoding="utf-8")
    resolver = SecretResolver()

    assert resolver.resolve("env:VERIDIX_TEST_KEY") == "secret-env"
    assert resolver.resolve(f"file:{secret_file}") == "secret-file"
    assert resolver.resolve("env:MISSING") is None


def test_secret_resolver_refs_file(tmp_path, monkeypatch) -> None:
    refs = tmp_path / "refs.json"
    refs.write_text(
        '{"provider.deepseek": {"env": "DEEPSEEK_API_KEY"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-ref")
    resolver = SecretResolver(refs)

    assert resolver.resolve("provider.deepseek") == "secret-ref"
    assert resolver.resolve("unknown.ref") is None
