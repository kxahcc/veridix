from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runners.container.resource_handle import ResourceManager, ResourceStatus


def test_resource_lifecycle_attach_detach_close() -> None:
    manager = ResourceManager()
    manager.create("sbx_1", "s2")

    manager.mark_ready("sbx_1")
    manager.attach("sbx_1")
    manager.detach("sbx_1")
    manager.attach("sbx_1")
    manager.close("sbx_1")

    assert manager.get("sbx_1").status == ResourceStatus.CLOSED


def test_heartbeat_loss_goes_stale_then_lost() -> None:
    manager = ResourceManager(stale_after_seconds=60, lost_after_seconds=300)
    manager.create("sbx_2", "s2")
    manager.mark_ready("sbx_2")
    manager.attach("sbx_2")

    now = datetime.now(timezone.utc)
    manager.reconcile(now=(now + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert manager.get("sbx_2").status == ResourceStatus.STALE

    manager.reconcile(now=(now + timedelta(seconds=400)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert manager.get("sbx_2").status == ResourceStatus.LOST


def test_attach_from_created_is_rejected() -> None:
    manager = ResourceManager()
    manager.create("sbx_3", "s2")

    with pytest.raises(ValueError, match="invalid transition"):
        manager.attach("sbx_3")
