from __future__ import annotations

import hashlib

import pytest

from services.release_service.offline_components import (
    assemble_offline_deps,
    build_offline_manifest,
    verify_offline_cache,
)


def _cache(tmp_path, name: str, version: str, data: bytes) -> None:
    (tmp_path / f"{name}-{version}.tar.gz").write_bytes(data)


def test_assemble_offline_deps_from_cache(tmp_path) -> None:
    npm_data = b"npm-package"
    py_data = b"python-wheel"
    _cache(tmp_path, "fastapi", "0.115.0", py_data)
    _cache(tmp_path, "lodash", "4.17.21", npm_data)
    manifest = build_offline_manifest(
        npm_lock={
            "packages": {
                "node_modules/lodash": {"name": "lodash", "version": "4.17.21"}
            }
        },
        python_lock="fastapi==0.115.0\n",
    )

    files = assemble_offline_deps(manifest, tmp_path)

    assert "deps/npm/lodash-4.17.21.tar.gz" in files
    assert "deps/python/fastapi-0.115.0.tar.gz" in files
    assert files["deps/python/fastapi-0.115.0.tar.gz"] == py_data


def test_offline_cache_hash_and_missing_detection(tmp_path) -> None:
    data = b"wheel"
    _cache(tmp_path, "pydantic", "2.8.0", data)
    good = build_offline_manifest(
        python_lock=(
            f"pydantic==2.8.0\n"
            f"  --hash=sha256:{hashlib.sha256(data).hexdigest()}\n"
        )
    )
    bad = build_offline_manifest(
        python_lock=(
            "pydantic==2.8.0\n"
            "  --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        )
    )

    assert verify_offline_cache(good, tmp_path) == []
    assert verify_offline_cache(bad, tmp_path) == ["hash:pydantic==2.8.0"]
    with pytest.raises(ValueError, match="offline cache incomplete"):
        assemble_offline_deps(
            build_offline_manifest(python_lock="missing==1.0.0\n"),
            tmp_path,
        )
