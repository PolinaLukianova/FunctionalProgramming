import asyncio
import os

CLIENTS = {}
ROOMS = {}
EVENT_QUEUE = None


async def send(writer, text):
    """Отправить строку клиенту."""
    try:
        writer.write((text + "\n").encode())
        await writer.drain()
    except Exception:
        pass


async def broadcast(room, text, exclude=None):
    """Отправить сообщение всем в комнате."""
    for w in list(ROOMS.get(room, [])):
        if w is not exclude:
            await send(w, text)


def cleanup_room(room):
    """Удалить комнату, если она пуста."""
    if room and room in ROOMS and len(ROOMS[room]) == 0:
        del ROOMS[room]
        print(f"Room '{room}' deleted (empty)")


async def event_dispatcher():
    """Обработчик событий из asyncio.Queue."""
    while True:
        event = await EVENT_QUEUE.get()
        try:
            if event["type"] == "broadcast":
                await broadcast(event["room"], event["text"], event.get("exclude"))
            elif event["type"] == "file_broadcast":
                room, filename, data, sender = event["room"], event["filename"], event["data"], event["sender"]
                for w in list(ROOMS.get(room, [])):
                    if w is not sender:
                        try:
                            w.write(f"/file {filename} {len(data)}\n".encode())
                            w.write(data)
                            await w.drain()
                        except Exception:
                            pass
        except Exception as e:
            print(f"Dispatcher error: {e}")
        finally:
            EVENT_QUEUE.task_done()


async def handle_client(reader, writer):
    """Обработка одного клиента."""
    CLIENTS[writer] = {"nick": None, "room": None}
    addr = writer.get_extra_info("peername")
    print(f"Connected: {addr}")
    await send(writer, "Welcome!")

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            msg = line.decode().strip()
            if not msg:
                continue

            if msg.startswith("/nick"):
                parts = msg.split(maxsplit=1)
                if len(parts) < 2:
                    await send(writer, "Usage: /nick name")
                    continue
                CLIENTS[writer]["nick"] = parts[1]
                await send(writer, f"Nick set: {parts[1]}")

            elif msg.startswith("/join"):
                parts = msg.split(maxsplit=1)
                if len(parts) < 2:
                    await send(writer, "Usage: /join room")
                    continue
                room = parts[1]
                old = CLIENTS[writer]["room"]
                if old:
                    ROOMS[old].discard(writer)
                    nick = CLIENTS[writer]["nick"] or "Anonim"
                    await broadcast(old, f"{nick} left the room")
                    # Удаляем пустую комнату
                    cleanup_room(old)
                CLIENTS[writer]["room"] = room
                ROOMS.setdefault(room, set()).add(writer)
                nick = CLIENTS[writer]["nick"] or "Anonim"
                await send(writer, f"Joined room {room}")
                await broadcast(room, f"{nick} joined the room")

            elif msg.startswith("/rooms"):
                if not ROOMS:
                    await send(writer, "No rooms available")
                else:
                    lines = [f"  {r} ({len(m)} users)" for r, m in ROOMS.items()]
                    await send(writer, "Available rooms:\n" + "\n".join(lines))

            elif msg.startswith("/who"):
                room = CLIENTS[writer]["room"]
                if not room:
                    await send(writer, "Join a room first")
                    continue
                members = ROOMS.get(room, set())
                if not members:
                    await send(writer, "Room is empty")
                    continue
                nicks = []
                for w in members:
                    info = CLIENTS.get(w, {})
                    nick = info.get("nick") or "Anonim"
                    if w is writer:
                        nick += " (you)"
                    nicks.append(nick)
                await send(writer, f"Users in '{room}' ({len(nicks)}):\n" + "\n".join(f"  • {n}" for n in nicks))

            elif msg.startswith("/pm"):
                parts = msg.split(maxsplit=2)
                if len(parts) < 3:
                    await send(writer, "Usage: /pm nick message")
                    continue
                target, text = parts[1], parts[2]
                sender = CLIENTS[writer]["nick"] or "Anonim"
                found = False
                for w, info in CLIENTS.items():
                    if info["nick"] == target:
                        found = True
                        await send(w, f"[PM from {sender}] {text}")
                        await send(writer, f"[PM to {target}] {text}")
                if not found:
                    await send(writer, "User not found")

            elif msg.startswith("/file"):
                parts = msg.split()
                if len(parts) < 3:
                    await send(writer, "Usage: /file filename size")
                    continue
                filename = os.path.basename(parts[1])
                try:
                    size = int(parts[2])
                except ValueError:
                    await send(writer, "Invalid file size")
                    continue
                room = CLIENTS[writer]["room"]
                if not room:
                    await send(writer, "Join room first")
                    await reader.readexactly(size)
                    continue
                data = await reader.readexactly(size)
                nick = CLIENTS[writer]["nick"] or "Anonim"
                await EVENT_QUEUE.put({
                    "type": "file_broadcast", "room": room,
                    "filename": filename, "data": data, "sender": writer
                })
                await send(writer, f"File sent: {filename}")
                await broadcast(room, f"{nick} sent file: {filename}", exclude=writer)

            elif msg.startswith("/quit"):
                break

            else:
                room = CLIENTS[writer]["room"]
                if not room:
                    await send(writer, "Join room first")
                    continue
                nick = CLIENTS[writer]["nick"] or "Anonim"
                await EVENT_QUEUE.put({
                    "type": "broadcast", "room": room,
                    "text": f"[{nick}] {msg}"
                })

    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception as e:
        print(f"Error: {e}")

    # Отключение
    nick = CLIENTS.get(writer, {}).get("nick") or "Anonim"
    room = CLIENTS.get(writer, {}).get("room")
    if room and room in ROOMS:
        ROOMS[room].discard(writer)
        if ROOMS[room]:
            await broadcast(room, f"{nick} left the room")
        # Удаляем пустую комнату
        cleanup_room(room)
    CLIENTS.pop(writer, None)
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    print(f"Disconnected: {addr}")


def reset_state():
    """Сброс состояния (для тестов)."""
    global EVENT_QUEUE
    CLIENTS.clear()
    ROOMS.clear()
    EVENT_QUEUE = asyncio.Queue()
    return EVENT_QUEUE


async def main():
    global EVENT_QUEUE
    EVENT_QUEUE = asyncio.Queue()
    asyncio.create_task(event_dispatcher())
    server = await asyncio.start_server(handle_client, "127.0.0.1", 8888)
    print("Server started on 127.0.0.1:8888")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())