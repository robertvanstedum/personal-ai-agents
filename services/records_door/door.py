"""records-door (ROOMS_R2.md §3.1): forwards TCP from this container's port
18881 to minimoi-records:18880, and nowhere else. It holds no credential and
reads nothing; Records still authenticates every request. Published on the
Mac only as 127.0.0.1:18881, so only local processes reach it."""
from __future__ import annotations

import asyncio
import os

TARGET_HOST = "minimoi-records"
TARGET_PORT = 18880
LISTEN_PORT = int(os.environ.get("DOOR_PORT", "18881"))
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
