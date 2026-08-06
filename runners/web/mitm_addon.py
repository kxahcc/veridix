from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getcwd())

from runners.web.proxy_gateway import ProxyCaptureAddon

addons = [
    ProxyCaptureAddon(
        os.environ["VERIDIX_CAPTURE_OUT"],
        max_response_bytes=int(os.environ.get("VERIDIX_CAPTURE_MAX_BYTES", "524288")),
        web_session_id=os.environ.get("VERIDIX_WEB_SESSION_ID", "web_session_wp06"),
        proxy_session_id=os.environ.get("VERIDIX_PROXY_SESSION_ID", "proxy_session_wp06"),
    )
]
