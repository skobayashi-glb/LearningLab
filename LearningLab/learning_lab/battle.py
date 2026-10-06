"""Adapter for the standalone C# battle host; never imports Unity."""
import hashlib
from pathlib import Path

from .rpc import JsonProcessEnvironment

HOST_DLL = Path(__file__).resolve().parents[1] / "BattleHost/bin/Release/net10.0/BattleHost.dll"


class BattleEnvironment(JsonProcessEnvironment):
    def __init__(self, width: int, height: int, units: int = 256, bases: int = 8,
                 party: int = 12, routes: int = 3, waypoints: int = 2, engine_sha256: str = "",
                 timeout_seconds: float = 60):
        if not HOST_DLL.exists():
            raise FileNotFoundError("Build first: dotnet build BattleHost/BattleHost.csproj -c Release")
        digest = hashlib.sha256(HOST_DLL.read_bytes()).hexdigest()
        if engine_sha256 and digest != engine_sha256.lower():
            raise ValueError("BattleHost changed: re-export the experiment before training")
        super().__init__(["dotnet", str(HOST_DLL), *map(str, (width, height, units, bases, party, routes, waypoints))],
                         timeout_seconds)
