#!/usr/bin/env python3
"""Simple HTTPS reverse proxy for Streamlit.
Terminates SSL and forwards to localhost:8501 (HTTP).
Supports both HTTP and WebSocket traffic.
"""
import asyncio
import ssl
import os

CERT = os.path.expanduser("~/code/certs/streamlit.crt")
KEY = os.path.expanduser("~/code/certs/streamlit.key")
LISTEN_PORT = 8502
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8501


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_client(client_reader, client_writer):
    try:
        backend_reader, backend_writer = await asyncio.open_connection(
            BACKEND_HOST, BACKEND_PORT
        )
    except Exception:
        client_writer.close()
        return

    t1 = asyncio.create_task(pipe(client_reader, backend_writer))
    t2 = asyncio.create_task(pipe(backend_reader, client_writer))

    await asyncio.gather(t1, t2, return_exceptions=True)


async def main():
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(CERT, KEY)

    server = await asyncio.start_server(
        handle_client, "0.0.0.0", LISTEN_PORT, ssl=ssl_ctx
    )
    print(f"HTTPS proxy listening on :{LISTEN_PORT} -> {BACKEND_HOST}:{BACKEND_PORT}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
