from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.agent_runtime.kernel.contracts import ExecutionRequest
from runners.web.protocol_testers import (
    GraphQLTesterRunner,
    WebSocketTesterRunner,
)


def _request(tool: str, **arguments) -> ExecutionRequest:
    return ExecutionRequest(
        action_id=f"action_{tool}",
        run_id="run_protocol",
        tool_ref=tool,
        input=arguments,
        idempotency_key=f"run_protocol:{tool}:1",
    )


class GraphQLHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        variables = body.get("variables", {})
        user_id = str(variables.get("id", "1"))
        payload = {
            "data": {
                "user": {
                    "id": user_id,
                    "name": f"user-{user_id}",
                }
            }
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover
        return


async def _echo_server(websocket):
    async for message in websocket:
        await websocket.send(message)


def test_graphql_tester_detects_object_swap_candidate() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), GraphQLHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/graphql"
        runner = GraphQLTesterRunner(timeout=5)

        result = runner.execute(
            _request(
                "web.graphql.test",
                endpoint=endpoint,
                query="query User($id: ID!) { user(id: $id) { id name } }",
                operation="User",
                variables={"id": "1"},
            )
        )

        assert result.status == "completed"
        categories = [
            obs.get("vuln_category")
            for obs in result.observations
        ]
        assert "graphql_authz" in categories
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_websocket_tester_marks_tampered_frame() -> None:
    import asyncio
    import threading
    import websockets

    async def serve():
        return await websockets.serve(
            _echo_server,
            "127.0.0.1",
            0,
        )

    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(serve())
    port = server.sockets[0].getsockname()[1]
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        runner = WebSocketTesterRunner(timeout=5)
        result = runner.execute(
            _request(
                "web.websocket.test",
                channel=f"ws://127.0.0.1:{port}",
                payload={"kind": "message", "userId": "1"},
            )
        )

        assert result.status == "completed"
        categories = [
            obs.get("vuln_category")
            for obs in result.observations
        ]
        assert "websocket_authz" in categories
    finally:
        loop.call_soon_threadsafe(server.close)
        asyncio.run_coroutine_threadsafe(
            server.wait_closed(),
            loop,
        ).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
