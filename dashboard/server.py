"""
ApexSpreadator — Dashboard Server
FastAPI backend with WebSocket support for real-time telemetry updates.
"""
import asyncio
import json
from typing import Optional, Set, Dict, Any
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from utils import get_logger

logger = get_logger("DashboardServer")


class DashboardServer:
    """
    FastAPI dashboard server for live telemetry tracking.
    """

    def __init__(self, agent=None):
        self.agent = agent
        self.app = FastAPI(title="ApexSpreadator Dashboard", version="1.0.0")
        self._websockets: Set[WebSocket] = set()
        self._setup_routes()

    def _setup_routes(self) -> None:
        static_dir = Path(__file__).parent / "static"

        @self.app.get("/")
        async def index():
            return FileResponse(static_dir / "index.html")

        @self.app.get("/api/account")
        async def get_account():
            if self.agent:
                return self.agent.get_account_data()
            return {"error": "Agent not connected"}

        @self.app.get("/api/positions")
        async def get_positions():
            if self.agent:
                return self.agent.get_positions_data()
            return []

        @self.app.get("/api/history")
        async def get_history():
            if self.agent:
                return self.agent.get_history_data()
            return []

        @self.app.get("/api/stats")
        async def get_stats():
            if self.agent:
                return self.agent.get_stats_data()
            return {}

        @self.app.get("/api/market_structure")
        async def get_market_structure():
            if self.agent:
                return self.agent.get_market_structure_data()
            return {}

        @self.app.get("/api/risk")
        async def get_risk():
            if self.agent:
                return self.agent.get_risk_data()
            return {}

        @self.app.post("/api/positions/{position_id}/close")
        async def close_position(position_id: str):
            if self.agent:
                result = await self.agent.manual_close_position(position_id)
                return result
            return {"error": "Agent not connected"}

        @self.app.post("/api/agent/pause")
        async def toggle_pause():
            if self.agent:
                return self.agent.toggle_pause()
            return {"error": "Agent not connected"}

        # WebSocket endpoint
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self._websockets.add(websocket)
            logger.info(f"WebSocket connected. Total clients: {len(self._websockets)}")
            try:
                while True:
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                        if msg.get("action") == "ping":
                            await websocket.send_json({"type": "pong"})
                    except json.JSONDecodeError:
                        pass
            except WebSocketDisconnect:
                self._websockets.discard(websocket)
                logger.info(f"WebSocket disconnected. Total clients: {len(self._websockets)}")

        self.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Broadcast updates to all active WebSocket clients."""
        if not self._websockets:
            return

        message = json.dumps({"type": event_type, "data": data})
        dead = set()

        for ws in self._websockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)

        self._websockets -= dead

    async def broadcast_full_update(self) -> None:
        """Broadcast a complete state packet."""
        if not self.agent or not self._websockets:
            return

        await self.broadcast("full_update", {
            "account": self.agent.get_account_data(),
            "positions": self.agent.get_positions_data(),
            "stats": self.agent.get_stats_data(),
            "market_structure": self.agent.get_market_structure_data(),
            "risk": self.agent.get_risk_data(),
        })
