from __future__ import annotations

from runners.container.resource_handle import ResourceHandle, ResourceManager
from runners.web.recovery import RecoveryLog, RecoveryRecord, decide_recovery


class BrowserSessionManager:
    def __init__(self) -> None:
        self._resources = ResourceManager()
        self._playwright = None

    def open(
        self,
        *,
        session_id: str,
        proxy_url: str,
        executable_path: str | None = None,
        ignore_https_errors: bool = False,
    ) -> ResourceHandle:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        launch_kwargs: dict = {
            "headless": True,
            "proxy": {"server": proxy_url},
        }
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        browser = self._playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(ignore_https_errors=ignore_https_errors)
        page = context.new_page()
        handle = self._resources.create(session_id, "browser")
        handle.metadata["browser"] = browser
        handle.metadata["page"] = page
        self._resources.mark_ready(session_id)
        self._resources.attach(session_id)
        return handle

    def navigate(self, handle: ResourceHandle, url: str) -> None:
        page = handle.metadata.get("page")
        if page is None:
            raise RuntimeError("browser session is not open")
        page.goto(url)

    def close(self, handle: ResourceHandle) -> None:
        browser = handle.metadata.get("browser")
        if browser is not None:
            browser.close()
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._resources.close(handle.resource_id)

    def recover(
        self,
        handle: ResourceHandle,
        *,
        proxy_url: str,
        executable_path: str | None = None,
        log: RecoveryLog | None = None,
        run_id: str | None = None,
    ) -> ResourceHandle:
        decision = decide_recovery(handle, reconnect_capability=True)
        if decision.action == "reuse":
            if log is not None:
                log.append(
                    _recovery_record(
                        handle,
                        resource_type="browser",
                        decision=decision,
                        reobserve_required=False,
                        run_id=run_id,
                    )
                )
            return handle
        if decision.action in ("reconnect", "revalidate"):
            browser = handle.metadata.get("browser")
            if browser is not None:
                browser.close()
            self._resources.close(handle.resource_id)
            rebuilt = self.open(
                session_id=f"{handle.resource_id}_recovered",
                proxy_url=proxy_url,
                executable_path=executable_path,
            )
            rebuilt.metadata["reobserve_required"] = True
            if log is not None:
                log.append(
                    _recovery_record(
                        handle,
                        resource_type="browser",
                        decision=decision,
                        new_resource_id=rebuilt.resource_id,
                        reobserve_required=True,
                        run_id=run_id,
                    )
                )
            return rebuilt
        if decision.action == "rebuild":
            rebuilt = self.open(
                session_id=f"{handle.resource_id}_recovered",
                proxy_url=proxy_url,
                executable_path=executable_path,
            )
            rebuilt.metadata["reobserve_required"] = True
            if log is not None:
                log.append(
                    _recovery_record(
                        handle,
                        resource_type="browser",
                        decision=decision,
                        new_resource_id=rebuilt.resource_id,
                        reobserve_required=True,
                        run_id=run_id,
                    )
                )
            return rebuilt
        raise RuntimeError(f"cannot recover resource: {decision.reason}")


def _recovery_record(
    handle: ResourceHandle,
    *,
    resource_type: str,
    decision,
    new_resource_id: str | None = None,
    reobserve_required: bool,
    run_id: str | None,
):
    return RecoveryRecord(
        resource_id=handle.resource_id,
        resource_type=resource_type,
        action=decision.action,
        reason=decision.reason,
        from_status=handle.status.value,
        new_resource_id=new_resource_id,
        reobserve_required=reobserve_required,
        run_id=run_id,
    )
