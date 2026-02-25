import asyncio
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from tkinter import simpledialog, filedialog
import queue
import os

HOST = "127.0.0.1"
PORT = 8888


# ---- Сетевая часть ----

async def network_task(reader, writer, in_q, out_q):
    async def read_loop():
        while True:
            line = await reader.readline()
            if not line:
                in_q.put("*** Disconnected ***")
                break
            text = line.decode().rstrip()
            if text.startswith("/file"):
                parts = text.split()
                if len(parts) >= 3:
                    filename = os.path.basename(parts[1])
                    size = int(parts[2])
                    data = await reader.readexactly(size)
                    path = os.path.join(os.getcwd(), filename)
                    with open(path, "wb") as f:
                        f.write(data)
                    in_q.put(f"File received: {path}")
                else:
                    in_q.put(text)
            else:
                in_q.put(text)

    async def write_loop():
        loop = asyncio.get_event_loop()
        while True:
            msg = await loop.run_in_executor(None, out_q.get)
            if isinstance(msg, tuple):
                filename, data = msg
                filename = os.path.basename(filename)
                writer.write(f"/file {filename} {len(data)}\n".encode())
                await writer.drain()
                writer.write(data)
                await writer.drain()
            else:
                writer.write((msg + "\n").encode())
                await writer.drain()
                if msg == "/quit":
                    break

    await asyncio.gather(read_loop(), write_loop())


async def start_connection(in_q, out_q):
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
        await network_task(reader, writer, in_q, out_q)
    except ConnectionRefusedError:
        in_q.put("*** Could not connect to server ***")
    except Exception as e:
        in_q.put(f"*** Connection error: {e} ***")


# ---- GUI ----

class ChatGUI:
    def __init__(self, root):
        self.root = root
        root.title("ChatChat")
        root.geometry("600x450")

        # Область сообщений
        self.output = ScrolledText(root, state="disabled", height=18)
        self.output.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        # Поле ввода + кнопка Send
        input_frame = tk.Frame(root)
        input_frame.pack(fill="x", padx=10, pady=(0, 5))

        self.entry = tk.Entry(input_frame)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.send())

        tk.Button(input_frame, text="Send", command=self.send).pack(side="left", padx=(5, 0))

        # Кнопки команд
        btn_frame = tk.Frame(root)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        tk.Button(btn_frame, text="Nick", command=self.set_nick).pack(side="left", padx=2)
        tk.Button(btn_frame, text="Join", command=self.join_room).pack(side="left", padx=2)
        tk.Button(btn_frame, text="Rooms", command=self.list_rooms).pack(side="left", padx=2)
        tk.Button(btn_frame, text="Who", command=self.who_in_room).pack(side="left", padx=2)
        tk.Button(btn_frame, text="PM", command=self.send_pm).pack(side="left", padx=2)
        tk.Button(btn_frame, text="File", command=self.send_file).pack(side="left", padx=2)
        tk.Button(btn_frame, text="Quit", command=self.quit).pack(side="left", padx=2)

        # Очереди
        self.in_q = queue.Queue()
        self.out_q = queue.Queue()
        self.poll()

    def poll(self):
        """Проверка входящих сообщений."""
        try:
            while True:
                msg = self.in_q.get_nowait()
                self.log(msg)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def log(self, text):
        self.output.config(state="normal")
        self.output.insert("end", text + "\n")
        self.output.config(state="disabled")
        self.output.see("end")

    def send(self):
        text = self.entry.get().strip()
        if text:
            self.out_q.put(text)
            self.entry.delete(0, "end")

    def set_nick(self):
        nick = simpledialog.askstring("Nick", "Enter nickname:", parent=self.root)
        if nick:
            self.out_q.put(f"/nick {nick}")

    def join_room(self):
        room = simpledialog.askstring("Join", "Enter room name:", parent=self.root)
        if room:
            self.out_q.put(f"/join {room}")

    def list_rooms(self):
        self.out_q.put("/rooms")

    def who_in_room(self):
        """Запросить список пользователей в текущей комнате."""
        self.out_q.put("/who")

    def send_pm(self):
        nick = simpledialog.askstring("PM", "Recipient nick:", parent=self.root)
        if not nick:
            return
        msg = simpledialog.askstring("PM", f"Message to {nick}:", parent=self.root)
        if msg:
            self.out_q.put(f"/pm {nick} {msg}")

    def send_file(self):
        path = filedialog.askopenfilename(parent=self.root)
        if not path:
            return
        with open(path, "rb") as f:
            data = f.read()
        self.out_q.put((path, data))

    def quit(self):
        self.out_q.put("/quit")
        self.root.destroy()


def main():
    root = tk.Tk()
    gui = ChatGUI(root)

    threading.Thread(
        target=lambda: asyncio.run(start_connection(gui.in_q, gui.out_q)),
        daemon=True
    ).start()

    root.mainloop()


if __name__ == "__main__":
    main()