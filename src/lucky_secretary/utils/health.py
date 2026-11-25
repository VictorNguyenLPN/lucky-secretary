"""Minimal async HTTP health check server for cloud platforms (e.g., Render Web Service)."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_OK_BODY = b'{"status":"ok"}'
_NOT_FOUND_BODY = b'{"error":"not_found"}'


class HealthCheckServer:
    """Zero-dependency asyncio HTTP server answering GET / and GET /health."""

    def __init__(self, port: int, host: str = "0.0.0.0") -> None:
        self._host = host
        self._requested_port = port
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        """Return the actual bound TCP port."""
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._requested_port

    async def start(self) -> None:
        """Start listening for HTTP health checks."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=self._requested_port,
        )
        logger.info("Health check server listening on %s:%s", self._host, self.port)

    async def stop(self) -> None:
        """Close the server gracefully."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("Health check server stopped.")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            parts = raw_line.decode("ascii", errors="ignore").strip().split()
            method = parts[0].upper() if len(parts) >= 1 else ""
            path = parts[1].split("?", 1)[0] if len(parts) >= 2 else ""

            if method in ("GET", "HEAD") and path in ("/", "/health"):
                body = b"" if method == "HEAD" else _OK_BODY
                header = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(_OK_BODY)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            else:
                body = _NOT_FOUND_BODY
                header = (
                    "HTTP/1.1 404 Not Found\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")

            writer.write(header + body)
            await writer.drain()
        except (TimeoutError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

