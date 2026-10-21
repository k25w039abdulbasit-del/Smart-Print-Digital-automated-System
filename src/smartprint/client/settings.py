"""Where a client finds its server + credentials (env > ~/.smartprint/client.json > demo file)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ClientSettings:
    server: str = "http://127.0.0.1:8000"
    pc_name: str = "PC-01"
    pc_key: str = ""

    @property
    def api(self) -> str:
        return self.server.rstrip("/") + "/api/v1"


def load_settings() -> ClientSettings:
    s = ClientSettings()
    for f in (Path.home() / ".smartprint" / "client.json", Path("client.json")):
        if f.exists():
            d = json.loads(f.read_text())
            s.server, s.pc_name, s.pc_key = d.get("server", s.server), d.get("pc_name", s.pc_name), d.get("pc_key", s.pc_key)
    s.server = os.environ.get("SMARTPRINT_SERVER", s.server)
    s.pc_name = os.environ.get("SMARTPRINT_PC_NAME", s.pc_name)
    s.pc_key = os.environ.get("SMARTPRINT_PC_KEY", s.pc_key)
    if not s.pc_key:      # developer convenience: credentials written by `smartprint-server seed-demo`
        demo = Path(os.environ.get("SMARTPRINT_DATA_DIR", "data")) / "demo_credentials.json"
        if demo.exists():
            s.pc_key = json.loads(demo.read_text()).get("pcs", {}).get(s.pc_name, "")
    return s
