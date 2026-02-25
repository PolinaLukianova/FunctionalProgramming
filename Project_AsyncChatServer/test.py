"""
Tests for the async chat server.

Run with:
    python -m pytest test_server.py -v
or:
    python test_server.py
"""

import asyncio
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import server


async def _read_line_safe(reader, timeout=3):
    raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return raw.decode().strip()


async def _drain_lines(reader, count, timeout=2):
    lines = []
    for _ in range(count):
        try:
            line = await _read_line_safe(reader, timeout=timeout)
            lines.append(line)
        except asyncio.TimeoutError:
            break
    return lines


async def _read_all_available(reader, timeout=1):
    lines = []
    while True:
        try:
            line = await _read_line_safe(reader, timeout=timeout)
            lines.append(line)
        except asyncio.TimeoutError:
            break
    return "\n".join(lines)


class TestSendFunction(unittest.TestCase):

    def test_send_writes_data(self):
        loop = asyncio.new_event_loop()

        async def _test():
            class MockWriter:
                def __init__(self):
                    self.data = bytearray()
                def write(self, data):
                    self.data.extend(data)
                async def drain(self):
                    pass

            w = MockWriter()
            await server.send(w, "Hello")
            self.assertEqual(w.data, b"Hello\n")

        loop.run_until_complete(_test())
        loop.close()

    def test_send_handles_broken_writer(self):
        loop = asyncio.new_event_loop()

        async def _test():
            class BrokenWriter:
                def write(self, data):
                    raise ConnectionResetError
                async def drain(self):
                    pass

            await server.send(BrokenWriter(), "test")

        loop.run_until_complete(_test())
        loop.close()


class TestBroadcast(unittest.TestCase):

    def test_broadcast_sends_to_all(self):
        loop = asyncio.new_event_loop()

        async def _test():
            class MockWriter:
                def __init__(self):
                    self.data = bytearray()
                def write(self, data):
                    self.data.extend(data)
                async def drain(self):
                    pass

            w1, w2 = MockWriter(), MockWriter()
            server.ROOMS["r"] = {w1, w2}
            await server.broadcast("r", "hi")
            self.assertEqual(w1.data, b"hi\n")
            self.assertEqual(w2.data, b"hi\n")
            del server.ROOMS["r"]

        loop.run_until_complete(_test())
        loop.close()

    def test_broadcast_with_exclude(self):
        loop = asyncio.new_event_loop()

        async def _test():
            class MockWriter:
                def __init__(self):
                    self.data = bytearray()
                def write(self, data):
                    self.data.extend(data)
                async def drain(self):
                    pass

            w1, w2 = MockWriter(), MockWriter()
            server.ROOMS["r"] = {w1, w2}
            await server.broadcast("r", "hi", exclude=w1)
            self.assertEqual(w1.data, b"")
            self.assertEqual(w2.data, b"hi\n")
            del server.ROOMS["r"]

        loop.run_until_complete(_test())
        loop.close()

    def test_broadcast_nonexistent_room(self):
        loop = asyncio.new_event_loop()

        async def _test():
            await server.broadcast("no_room", "hi")

        loop.run_until_complete(_test())
        loop.close()


class TestEventQueue(unittest.TestCase):

    def test_event_queue_creation(self):
        eq = server.reset_state()
        self.assertIsInstance(eq, asyncio.Queue)

    def test_event_dispatcher(self):
        loop = asyncio.new_event_loop()

        async def _test():
            class MockWriter:
                def __init__(self):
                    self.data = bytearray()
                def write(self, data):
                    self.data.extend(data)
                async def drain(self):
                    pass

            eq = server.reset_state()
            w = MockWriter()
            server.ROOMS["dr"] = {w}
            await eq.put({"type": "broadcast", "room": "dr", "text": "queued"})
            task = asyncio.create_task(server.event_dispatcher())
            await asyncio.sleep(0.3)
            task.cancel()
            self.assertEqual(w.data, b"queued\n")

        loop.run_until_complete(_test())
        loop.close()


class TestClientHandler(unittest.TestCase):

    def test_nick_and_join(self):
        loop = asyncio.new_event_loop()

        async def _test():
            server.reset_state()
            disp = asyncio.create_task(server.event_dispatcher())
            srv = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", port)
            welcome = await _read_line_safe(r)
            self.assertIn("Welcome", welcome)

            w.write(b"/nick TestUser\n")
            await w.drain()
            resp = await _read_line_safe(r)
            self.assertIn("Nick set: TestUser", resp)

            w.write(b"/join lobby\n")
            await w.drain()
            lines = await _drain_lines(r, 2, timeout=2)
            combined = " ".join(lines)
            self.assertIn("Joined room lobby", combined)
            self.assertIn("joined the room", combined)

            w.write(b"/rooms\n")
            await w.drain()
            rooms_resp = await _read_all_available(r, timeout=1)
            self.assertIn("lobby", rooms_resp)

            w.write(b"/quit\n")
            await w.drain()
            await asyncio.sleep(0.3)
            w.close()
            await w.wait_closed()
            srv.close()
            await srv.wait_closed()
            disp.cancel()

        loop.run_until_complete(_test())
        loop.close()

    def test_private_message(self):
        loop = asyncio.new_event_loop()

        async def _test():
            server.reset_state()
            disp = asyncio.create_task(server.event_dispatcher())
            srv = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]

            ra, wa = await asyncio.open_connection("127.0.0.1", port)
            await _read_line_safe(ra)
            wa.write(b"/nick Alice\n")
            await wa.drain()
            await _read_line_safe(ra)

            rb, wb = await asyncio.open_connection("127.0.0.1", port)
            await _read_line_safe(rb)
            wb.write(b"/nick Bob\n")
            await wb.drain()
            await _read_line_safe(rb)

            wa.write(b"/pm Bob Hello!\n")
            await wa.drain()
            resp_a = await _read_line_safe(ra)
            self.assertIn("[PM to Bob]", resp_a)
            resp_b = await _read_line_safe(rb)
            self.assertIn("[PM from Alice]", resp_b)

            wa.write(b"/pm Nobody hi\n")
            await wa.drain()
            resp = await _read_line_safe(ra)
            self.assertIn("not found", resp)

            wa.close()
            wb.close()
            await wa.wait_closed()
            await wb.wait_closed()
            srv.close()
            await srv.wait_closed()
            disp.cancel()

        loop.run_until_complete(_test())
        loop.close()

    def test_room_broadcast(self):
        loop = asyncio.new_event_loop()

        async def _test():
            server.reset_state()
            disp = asyncio.create_task(server.event_dispatcher())
            srv = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]

            ra, wa = await asyncio.open_connection("127.0.0.1", port)
            await _read_line_safe(ra)
            wa.write(b"/nick Alice\n")
            await wa.drain()
            await _read_line_safe(ra)
            wa.write(b"/join general\n")
            await wa.drain()
            await _drain_lines(ra, 2, timeout=2)

            rb, wb = await asyncio.open_connection("127.0.0.1", port)
            await _read_line_safe(rb)
            wb.write(b"/nick Bob\n")
            await wb.drain()
            await _read_line_safe(rb)
            wb.write(b"/join general\n")
            await wb.drain()
            await _drain_lines(rb, 2, timeout=2)
            await _drain_lines(ra, 1, timeout=2)

            wa.write(b"Hello!\n")
            await wa.drain()
            await asyncio.sleep(0.5)

            resp_a = await _read_line_safe(ra, timeout=3)
            self.assertIn("[Alice] Hello!", resp_a)
            resp_b = await _read_line_safe(rb, timeout=3)
            self.assertIn("[Alice] Hello!", resp_b)

            wa.close()
            wb.close()
            await wa.wait_closed()
            await wb.wait_closed()
            srv.close()
            await srv.wait_closed()
            disp.cancel()

        loop.run_until_complete(_test())
        loop.close()

    def test_disconnect(self):
        loop = asyncio.new_event_loop()

        async def _test():
            server.reset_state()
            disp = asyncio.create_task(server.event_dispatcher())
            srv = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", port)
            await _read_line_safe(r)
            w.write(b"/nick X\n")
            await w.drain()
            await _read_line_safe(r)
            w.write(b"/join room1\n")
            await w.drain()
            await _drain_lines(r, 2, timeout=2)

            w.close()
            await w.wait_closed()
            await asyncio.sleep(1.0)

            self.assertTrue(
                "room1" not in server.ROOMS or
                len(server.ROOMS.get("room1", set())) == 0
            )

            srv.close()
            await srv.wait_closed()
            disp.cancel()

        loop.run_until_complete(_test())
        loop.close()

    def test_message_without_room(self):
        loop = asyncio.new_event_loop()

        async def _test():
            server.reset_state()
            disp = asyncio.create_task(server.event_dispatcher())
            srv = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", port)
            await _read_line_safe(r)
            w.write(b"hello\n")
            await w.drain()
            resp = await _read_line_safe(r)
            self.assertIn("Join room first", resp)

            w.close()
            await w.wait_closed()
            srv.close()
            await srv.wait_closed()
            disp.cancel()

        loop.run_until_complete(_test())
        loop.close()

    def test_rooms_empty(self):
        loop = asyncio.new_event_loop()

        async def _test():
            server.reset_state()
            disp = asyncio.create_task(server.event_dispatcher())
            srv = await asyncio.start_server(server.handle_client, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", port)
            await _read_line_safe(r)
            w.write(b"/rooms\n")
            await w.drain()
            resp = await _read_line_safe(r)
            self.assertIn("No rooms", resp)

            w.close()
            await w.wait_closed()
            srv.close()
            await srv.wait_closed()
            disp.cancel()

        loop.run_until_complete(_test())
        loop.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)