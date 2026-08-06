from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from services.agent_runtime.tool_network import resolve_tool_network


def build_worker_runner_factory(
    *,
    runner_kind: str | None = None,
    docker_backend_factory: Callable[[], Any] | None = None,
    web_runner: Any | None = None,
) -> Callable[[], Any]:
    kind = runner_kind or os.environ.get("VERIDIX_RUNNER", "docker")
    if kind == "fake":
        from services.agent_runtime.kernel.fake_runner import FakeRunner
        from services.agent_runtime.kernel.skill_bundle_runner import (
            SkillBundleRunner,
        )
        from services.agent_runtime.kernel.composite_tool_runner import (
            CompositeToolRunner,
        )
        from runners.web.protocol_testers import (
            GraphQLTesterRunner,
            WebSocketTesterRunner,
        )
        from runners.web.authz_runner import AuthzMatrixRunner
        from runners.web.ssrf_runner import SSRFTesterRunner
        from runners.remote.oast_tool import OastToolRunner
        from pathlib import Path as _Path
        import os as _os

        graphql_tester = GraphQLTesterRunner()
        websocket_tester = WebSocketTesterRunner()
        authz_tester = AuthzMatrixRunner()
        ssrf_tester = SSRFTesterRunner()
        oast_tester = OastToolRunner(
            db=str(
                _Path(_os.environ.get("VERIDIX_RUNTIME_DIR", "runtime"))
                / "oast.db"
            ),
            base_url=_os.environ.get(
                "VERIDIX_OAST_BASE_URL",
                "http://127.0.0.1:8791",
            ),
        )
        skill_reader = SkillBundleRunner(
            _Path(__file__).resolve().parents[3]
        )

        def fake_factory() -> CompositeToolRunner:
            return CompositeToolRunner(
                {
                    "skill.read": skill_reader,
                    "web.graphql.test": graphql_tester,
                    "web.websocket.test": websocket_tester,
                    "web.authz.test": authz_tester,
                    "web.ssrf.test": ssrf_tester,
                    "oast.create": oast_tester,
                    "oast.check": oast_tester,
                },
                default=FakeRunner(),
            )

        return fake_factory
    if kind == "docker":
        try:
            from runners.container.runner_port import DockerSandboxBackend
            from runners.container.sandbox_spec import SandboxSpec
            from services.agent_runtime.kernel.composite_tool_runner import (
                CompositeToolRunner,
            )
            from services.agent_runtime.kernel.sandbox_tool_runner import (
                SandboxToolRunner,
            )
            from services.agent_runtime.kernel.skill_bundle_runner import (
                SkillBundleRunner,
            )
            from services.agent_runtime.kernel.web_tool_runner import (
                WebToolRunner,
            )
            from services.tool_pack.registry import ToolRegistry
            from services.tool_pack.execution import ContainerToolRunner
            from runners.web.replay_tool import WebReplayRunner
            from runners.web.protocol_testers import (
                GraphQLTesterRunner,
                WebSocketTesterRunner,
            )
            from runners.web.authz_runner import AuthzMatrixRunner
            from runners.web.ssrf_runner import SSRFTesterRunner
            from runners.web.web_vuln_testers import (
                FileUploadTesterRunner,
                LFITesterRunner,
            )
            from runners.web.owasp_tester import OwaspTesterRunner
            from runners.web.dom_xss_tester import DomXssTesterRunner

            backend = (
                docker_backend_factory()
                if docker_backend_factory is not None
                else DockerSandboxBackend()
            )
            image = os.environ.get(
                "VERIDIX_DOCKER_IMAGE",
                "ghcr.io/kxahc/veridix/veridix-tools:full",
            )
            workspace = Path(os.environ.get("VERIDIX_WORKSPACE_DIR", os.getcwd()))
            runtime_dir = Path(
                os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")
            ).resolve()
            tool_output_dir = runtime_dir / "tool-output"
            tool_output_dir.mkdir(parents=True, exist_ok=True)
            web = (
                web_runner
                if web_runner is not None
                else WebToolRunner(str(workspace))
            )
            replay = WebReplayRunner(lambda: web.observations())
            graphql_tester = GraphQLTesterRunner()
            websocket_tester = WebSocketTesterRunner()
            authz_tester = AuthzMatrixRunner()
            ssrf_tester = SSRFTesterRunner()
            file_upload_tester = FileUploadTesterRunner()
            lfi_tester = LFITesterRunner()
            owasp_tester = OwaspTesterRunner()
            dom_xss_tester = DomXssTesterRunner()
            from runners.web.connector_tool import (
                ConnectorToolRunner,
                UnavailableToolRunner,
            )
            from runners.remote.oast_tool import OastToolRunner

            connector_runners = {}
            zap_url = os.environ.get("VERIDIX_ZAP_URL")
            if zap_url:
                from runners.web.zap_connector import ZapConnector

                connector_runners["zap.scan"] = ConnectorToolRunner(
                    ZapConnector(
                        base_url=zap_url,
                        api_key=os.environ.get(
                            "VERIDIX_ZAP_API_KEY",
                            "veridix-zap",
                        ),
                    ),
                    "zap",
                )
            caido_url = os.environ.get("VERIDIX_CAIDO_URL")
            if caido_url:
                from runners.web.caido_connector import CaidoConnector

                connector_runners["caido.scan"] = ConnectorToolRunner(
                    CaidoConnector(base_url=caido_url),
                    "caido",
                )
            burp_url = os.environ.get("VERIDIX_BURP_URL")
            if burp_url:
                from runners.web.burp_connector import BurpConnector

                connector_runners["burp.scan"] = ConnectorToolRunner(
                    BurpConnector(base_url=burp_url),
                    "burp",
                )
            for ref in ("zap.scan", "caido.scan", "burp.scan"):
                connector_runners.setdefault(
                    ref,
                    UnavailableToolRunner(ref),
                )
            oast_runner = OastToolRunner(
                db=Path(
                    os.environ.get("VERIDIX_RUNTIME_DIR", "runtime")
                )
                / "oast.db",
                base_url=os.environ.get(
                    "VERIDIX_OAST_BASE_URL",
                    "http://127.0.0.1:8791",
                ),
            )
            registry = ToolRegistry()
            pack_dir = Path(__file__).resolve().parents[3] / "deploy" / "toolpacks"
            for pack_path in sorted(pack_dir.glob("*.json")):
                registry.load_manifest(pack_path)
            mismatches = registry.verify_local_image_digests()
            if mismatches:
                raise RuntimeError(
                    "tool pack image digest mismatch with the local image; "
                    "rebuild veridix-tools:full and update deploy/toolpacks: "
                    + ", ".join(mismatches)
                )
            container_runners = {}
            skill_reader = SkillBundleRunner(
                Path(__file__).resolve().parents[3]
            )
            tool_network = resolve_tool_network()
            for definition in registry.list():
                if (
                    definition.runner != "container"
                    or definition.ref == "shell.probe"
                ):
                    continue
                container_runners[definition.ref] = ContainerToolRunner(
                    manifest=registry.pack_for(definition.ref),
                    definition=definition,
                    network=tool_network,
                    mounts=(
                        {
                            "source": workspace.resolve().as_posix(),
                            "target": "/workspace/input",
                            "mode": "ro",
                        },
                        {
                            "source": tool_output_dir.as_posix(),
                            "target": "/workspace/output",
                            "mode": "rw",
                        },
                    ),
                )

            def factory() -> CompositeToolRunner:
                sandbox_runner = SandboxToolRunner(
                    backend,
                    SandboxSpec(
                        sandbox_profile="S2",
                        image_digest=image,
                        uid=0,
                        gid=0,
                    ),
                )
                return CompositeToolRunner(
                    {
                        "skill.read": skill_reader,
                        "shell.probe": sandbox_runner,
                        **container_runners,
                        "browser.open": web,
                        "proxy.list": web,
                        "web.replay": replay,
                        "web.graphql.test": graphql_tester,
                        "web.websocket.test": websocket_tester,
                        "web.authz.test": authz_tester,
                        "web.ssrf.test": ssrf_tester,
                        "web.file-upload.test": file_upload_tester,
                        "web.lfi.test": lfi_tester,
                        "web.owasp.test": owasp_tester,
                        "web.dom-xss.test": dom_xss_tester,
                        **connector_runners,
                        "oast.create": oast_runner,
                        "oast.check": oast_runner,
                    }
                )

            return factory
        except Exception as error:
            raise RuntimeError(
                "docker runner unavailable; fix Docker or set VERIDIX_RUNNER=fake "
                "only for local development: "
                + str(error)
            ) from error
    raise ValueError(f"unknown VERIDIX_RUNNER value: {kind}")
