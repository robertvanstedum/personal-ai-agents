"""Dedicated intelligence loopback door, following the existing Records pattern."""
from __future__ import annotations

import asyncio
import os

TARGET_HOST = "intelligence"
TARGET_PORT = 18882
LISTEN_PORT = int(os.environ.get("DOOR_PORT", "18882"))
MAX_CONNECTIONS = 32
IDLE_S = 300

slots = asyncio.Semaphore(MAX_CONNECTIONS)


async def pipe(reader, writer):
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), timeout=IDLE_S)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(client_reader, client_writer):
    if slots.locked():
        client_writer.close()
        return
    async with slots:
        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(TARGET_HOST, TARGET_PORT), timeout=5)
        except (OSError, asyncio.TimeoutError):
            client_writer.close()
            return
        await asyncio.gather(pipe(client_reader, up_writer), pipe(up_reader, client_writer))


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
