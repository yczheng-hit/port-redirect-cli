"""TCP proxy engine using asyncio."""

import asyncio
import logging
import socket

logger = logging.getLogger("port-redirect.proxy")


def _set_nodelay(writer):
    """Enable TCP_NODELAY on a connection to reduce latency."""
    try:
        sock = writer.transport.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except (OSError, AttributeError, TypeError):
        pass


class ProxyServer:
    """Asyncio-based TCP proxy that forwards local port traffic to a remote target."""

    def __init__(self, listen_port: int, target_host: str, target_port: int):
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self._server: asyncio.AbstractServer | None = None

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, label: str):
        """Relay data from reader to writer until EOF, then close writer."""
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass

    async def _handle_client(self, local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter):
        """Handle an incoming connection: connect to target and relay bidirectionally."""
        remote_reader: asyncio.StreamReader | None = None
        remote_writer: asyncio.StreamWriter | None = None
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(self.target_host, self.target_port),
                timeout=30,
            )
        except (OSError, asyncio.TimeoutError) as e:
            logger.error("Failed to connect to %s:%s — %s", self.target_host, self.target_port, e)
            local_writer.close()
            await local_writer.wait_closed()
            return

        peername = local_writer.get_extra_info("peername")
        logger.info("Proxy connection %s -> %s:%s", peername, self.target_host, self.target_port)

        _set_nodelay(local_writer)
        _set_nodelay(remote_writer)

        task_a = asyncio.create_task(self._relay(local_reader, remote_writer, "L->R"))
        task_b = asyncio.create_task(self._relay(remote_reader, local_writer, "R->L"))

        done, pending = await asyncio.wait(
            [task_a, task_b],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()

        logger.info("Proxy connection %s closed", peername)

    async def start(self):
        """Start listening for connections."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host="0.0.0.0",
            port=self.listen_port,
        )
        addr = self._server.sockets[0].getsockname()
        logger.info("Proxy listening on 0.0.0.0:%s -> %s:%s", addr[1], self.target_host, self.target_port)

    async def serve_forever(self):
        """Run the server until cancelled."""
        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Gracefully stop the server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Proxy on port %s stopped", self.listen_port)