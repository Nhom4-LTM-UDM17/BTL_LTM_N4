import asyncio
import json
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
from queue import Queue, Empty
import time

HOST = "192.168.227.92"
PORT = 7777
BOARD_SIZE = 15


class GuiClient:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('Caro Online')
        self.root.geometry("1100x700")
        self.root.config(bg="#1e1e2f")

        # Network + state
        self.queue = Queue()
        self.reader = None
        self.writer = None
        self.loop = None
        self.name = ''
        self.in_match = False
        self.you = None
        self.opponent = None
        self.turn = None
        self.deadline = None
        self.timer_id = None
        self.highlighted = []
        self.resize_debounce = None

        # Trạng thái bàn cờ
        self.board_state = [['' for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.cell_size = 0
        self.offset_x = 0
        self.offset_y = 0

        # ========== HEADER ==========
        header = tk.Frame(root, bg="#252539", pady=10)
        header.pack(side='top', fill='x')
        tk.Label(header, text='Name:', bg="#252539", fg="white").pack(side='left', padx=5)
        self.name_var = tk.StringVar(value='Player')
        tk.Entry(header, textvariable=self.name_var, width=15, bg="#333347", fg="white", insertbackground="white").pack(side='left', padx=5)
        self.connect_btn = tk.Button(header, text='Connect', command=self.on_connect, bg="#0078D7", fg="white", relief='flat')
        self.connect_btn.pack(side='left', padx=5)
        self.disconnect_btn = tk.Button(header, text='Disconnect', command=self.on_disconnect, bg="#d9534f", fg="white", relief='flat', state='disabled')
        self.disconnect_btn.pack(side='left', padx=5)

        # ========== INFO BAR (THÔNG BÁO + TIMER) ==========
        info_bar = tk.Frame(root, bg="#1e1e2f", height=30)
        info_bar.pack(side='top', fill='x', pady=(0, 5))
        info_bar.pack_propagate(False)

        self.status_var = tk.StringVar(value='Not connected')
        self.status_label = tk.Label(info_bar, textvariable=self.status_var, bg="#1e1e2f",
                                     fg="#FFD700", font=("Segoe UI", 10, "italic"), anchor='w')
        self.status_label.pack(side='left', padx=15, fill='x', expand=True)

        self.timer_var = tk.StringVar(value='')
        self.timer_label = tk.Label(info_bar, textvariable=self.timer_var,
                                    bg="#1e1e2f", fg="#00FFAA", font=("Consolas", 12, "bold"))
        self.timer_label.pack(side='right', padx=20)

        # ========== LEFT PANEL ==========
        left_panel = tk.Frame(root, bg="#2b2b3c", width=180)
        left_panel.pack(side='left', fill='y')
        tk.Label(left_panel, text='Online Users', bg="#2b2b3c", fg="#00FFAA", font=("Segoe UI", 12, "bold")).pack(pady=10)
        self.users_listbox = tk.Listbox(left_panel, height=15, bg="#1e1e2f", fg="white", selectbackground="#00FFAA", relief='flat')
        self.users_listbox.pack(fill='y', padx=8)
        self.challenge_btn = tk.Button(left_panel, text='Challenge', command=self.on_challenge, bg="#00b894", fg="white", relief='flat', state='disabled')
        self.challenge_btn.pack(pady=10)

        # ========== CENTER: BOARD (CANVAS 3D) ==========
        center_frame = tk.Frame(root, bg="#1e1e2f")
        center_frame.pack(side='left', expand=True, fill='both', padx=10, pady=(0, 10))

        self.canvas = tk.Canvas(center_frame, bg="#1e1e2f", highlightthickness=0)
        self.canvas.pack(expand=True, fill='both')

        self.canvas.bind('<Configure>', self.on_canvas_configure)
        self.root.after(200, self.on_canvas_resize)

        # ========== RIGHT PANEL: CHAT ==========
        right_panel = tk.Frame(root, bg="#2b2b3c", width=250)
        right_panel.pack(side='right', fill='y')
        tk.Label(right_panel, text='Chat', bg="#2b2b3c", fg="#FFD700", font=("Segoe UI", 12, "bold")).pack(pady=5)

        self.chat_area = scrolledtext.ScrolledText(right_panel, width=30, height=25, bg="#1e1e2f", fg="white", wrap='word', state='disabled')
        self.chat_area.pack(padx=10, pady=5, fill='both', expand=True)

        chat_entry_frame = tk.Frame(right_panel, bg="#2b2b3c")
        chat_entry_frame.pack(fill='x', padx=10, pady=5)
        self.chat_entry = tk.Entry(chat_entry_frame, bg="#333347", fg="white", relief='flat')
        self.chat_entry.pack(side='left', fill='x', expand=True, padx=(0, 5))
        self.chat_entry.bind('<Return>', self.on_send_chat)
        tk.Button(chat_entry_frame, text='Send', command=self.on_send_chat, bg="#00AEEF", fg="white", relief='flat').pack(side='right')

        self.root.after(100, self.process_queue)

    # ================== 3D BOARD ==================
    def on_canvas_configure(self, event=None):
        if self.resize_debounce:
            self.root.after_cancel(self.resize_debounce)
        self.resize_debounce = self.root.after(50, self.on_canvas_resize)

    def on_canvas_resize(self):
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return

        self.cell_size = min(width, height) // BOARD_SIZE
        self.canvas.delete('all')

        self.offset_x = (width - self.cell_size * BOARD_SIZE) // 2
        self.offset_y = (height - self.cell_size * BOARD_SIZE) // 2

        # Viền lưới
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                x1 = self.offset_x + x * self.cell_size
                y1 = self.offset_y + y * self.cell_size
                x2 = x1 + self.cell_size
                y2 = y1 + self.cell_size
                self.canvas.create_rectangle(x1, y1, x2, y2, outline="#3a3a50", width=1, fill="")

        self.redraw_board_from_state()

    def draw_3d_cell(self, x, y, base_color="#2b2b3c", symbol='', text_color="#FFFFFF"):
        if self.cell_size < 10:
            return

        x1 = self.offset_x + x * self.cell_size
        y1 = self.offset_y + y * self.cell_size
        x2 = x1 + self.cell_size
        y2 = y1 + self.cell_size

        shadow_offset = max(2, self.cell_size // 15)
        light_offset = max(1, self.cell_size // 20)

        tag = f"cell_3d_{x}_{y}"
        self.canvas.delete(tag)

        # Bóng đổ
        self.canvas.create_rectangle(
            x1 + shadow_offset, y1 + shadow_offset, x2 + shadow_offset, y2 + shadow_offset,
            fill="#1a1a2e", outline="", tags=tag
        )

        # Mặt trên
        self.canvas.create_rectangle(
            x1, y1, x2, y2,
            fill=base_color, outline="", tags=tag
        )

        # Viền sáng
        self.canvas.create_polygon(
            x1, y1, x1 + light_offset, y1 + light_offset,
            x2 - light_offset, y1 + light_offset, x2, y1,
            fill="#4a4a60", outline="", tags=tag
        )
        self.canvas.create_polygon(
            x1, y1, x1 + light_offset, y1 + light_offset,
            x1 + light_offset, y2 - light_offset, x1, y2,
            fill="#4a4a60", outline="", tags=tag
        )

        # X/O với bóng chữ
        if symbol:
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            font_size = max(14, self.cell_size // 3)

            self.canvas.create_text(
                center_x + 1, center_y + 1,
                text=symbol, font=("Consolas", font_size, "bold"),
                fill="#000000", tags=tag
            )
            self.canvas.create_text(
                center_x, center_y,
                text=symbol, font=("Consolas", font_size, "bold"),
                fill=text_color, tags=tag
            )

        # Bind click chỉ cho ô trống
        if not symbol:
            self.canvas.tag_bind(tag, '<Button-1>', lambda e, xx=x, yy=y: self.on_cell(xx, yy))

    def redraw_board_from_state(self):
        # Vẽ tất cả ô
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                symbol = self.board_state[y][x]
                base_color = "#2b2b3c"
                text_color = "#FFFFFF"

                if symbol == "X":
                    base_color = "#0078D7"
                elif symbol == "O":
                    base_color = "#FF3B30"

                self.draw_3d_cell(x, y, base_color, symbol, text_color)

        # VẼ HIGHLIGHT VÀNG SAU CÙNG
        self.draw_highlights()

    def draw_highlights(self):
        self.canvas.delete("highlight")
        if not self.highlighted:
            return

        for (x, y) in self.highlighted:
            if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
                continue
            x1 = self.offset_x + x * self.cell_size
            y1 = self.offset_y + y * self.cell_size
            x2 = x1 + self.cell_size
            y2 = y1 + self.cell_size

            # Viền vàng đậm
            outer = self.canvas.create_rectangle(
                x1 - 4, y1 - 4, x2 + 4, y2 + 4,
                outline="#FFD700", width=5, tags="highlight"
            )
            # Viền trắng trong (glow)
            inner = self.canvas.create_rectangle(
                x1 - 2, y1 - 2, x2 + 2, y2 + 2,
                outline="#FFFFFF", width=2, tags="highlight"
            )
            self.canvas.tag_raise(outer)
            self.canvas.tag_raise(inner)

    def set_cell(self, x, y, symbol):
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return
        self.board_state[y][x] = symbol
        base_color = "#0078D7" if symbol == "X" else "#FF3B30"
        self.draw_3d_cell(x, y, base_color, symbol, "#FFFFFF")
        self.draw_highlights()

    def clear_board(self):
        self.board_state = [['' for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.highlighted = []
        self.canvas.delete('all')
        if self.cell_size > 0:
            self.on_canvas_resize()

    def highlight_winning_line(self, cells):
        self.highlighted = cells
        self.redraw_board_from_state()

    def enable_board(self):
        if not self.in_match or self.you != self.turn:
            return
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                if not self.board_state[y][x]:
                    tag = f"cell_3d_{x}_{y}"
                    self.canvas.tag_bind(tag, '<Button-1>', lambda e, xx=x, yy=y: self.on_cell(xx, yy))

    def disable_board(self):
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                if not self.board_state[y][x]:
                    tag = f"cell_3d_{x}_{y}"
                    try:
                        self.canvas.tag_unbind(tag, '<Button-1>')
                    except:
                        pass

    # ================== NETWORK ==================
    def on_connect(self):
        if self.writer:
            messagebox.showinfo('Info', 'Already connected')
            return
        self.name = self.name_var.get().strip()
        if not self.name:
            self.name = "Player"
            self.name_var.set("Player")
        self.set_status('Connecting...')
        self.connect_btn['state'] = 'disabled'
        threading.Thread(target=self.start_async_loop, daemon=True).start()

    def on_disconnect(self):
        self.set_status('Disconnecting...')
        if self.writer and self.loop:
            self.loop.call_soon_threadsafe(self.writer.close)

    def on_challenge(self):
        sel = self.users_listbox.curselection()
        if not sel:
            messagebox.showinfo('Info', 'Select a user to challenge')
            return
        opponent = self.users_listbox.get(sel[0])
        if opponent == self.name:
            messagebox.showinfo('Info', 'Cannot challenge yourself')
            return
        self.send_json({'type': 'challenge', 'opponent': opponent})

    def on_cell(self, x, y):
        if not self.in_match or self.you != self.turn:
            return
        if self.board_state[y][x]:
            return
        self.disable_board()
        self.send_json({'type': 'move', 'x': x, 'y': y})

    def on_send_chat(self, event=None):
        text = self.chat_entry.get().strip()
        if not text or not self.in_match:
            return
        self.send_json({'type': 'chat', 'text': text})
        self.append_chat(f'You: {text}\n', "you")
        self.chat_entry.delete(0, tk.END)

    def start_async_loop(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.async_main())
        except Exception as e:
            print(f"Async loop error: {e}")
            self.queue.put((self.handle_disconnect, ()))
        finally:
            if self.loop:
                self.loop.close()
            self.loop = None

    async def async_main(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
        except Exception as e:
            self.queue.put((self.set_status, (f'Connect failed: {e}',)))
            self.queue.put((self.handle_disconnect, ()))
            return

        await self.send_json_async({'type': 'login', 'name': self.name})

        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                msg = json.loads(line.decode('utf-8').strip())
                self.queue.put((self.handle_msg, (msg,)))
        finally:
            self.queue.put((self.handle_disconnect, ()))
            if self.writer:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except:
                    pass

    async def send_json_async(self, obj):
        if not self.writer or self.writer.is_closing():
            return
        data = json.dumps(obj, ensure_ascii=False) + '\n'
        self.writer.write(data.encode('utf-8'))
        await self.writer.drain()

    def send_json(self, obj):
        if not self.writer or not self.loop or self.writer.is_closing():
            messagebox.showinfo('Info', 'Not connected')
            return
        asyncio.run_coroutine_threadsafe(self.send_json_async(obj), self.loop)

    # ================== UI HELPERS ==================
    def process_queue(self):
        try:
            while True:
                fn, args = self.queue.get_nowait()
                fn(*args)
        except Empty:
            pass
        self.root.after(100, self.process_queue)

    def set_status(self, text):
        self.status_var.set(text)

    def append_chat(self, text, tag=None):
        self.chat_area.config(state='normal')
        if tag == "you":
            self.chat_area.insert(tk.END, text, ("you",))
        else:
            self.chat_area.insert(tk.END, text)
        self.chat_area.config(state='disabled')
        self.chat_area.see(tk.END)

    # ================== COUNTDOWN ==================
    def start_countdown(self, deadline):
        if not deadline:
            return
        self.deadline = deadline
        self.update_timer()

    def update_timer(self):
        if not self.deadline:
            self.timer_var.set('')
            return

        remaining = int(self.deadline - time.time())
        if remaining > 0:
            self.timer_var.set(f"{remaining}s left")
            self.timer_id = self.root.after(1000, self.update_timer)
        else:
            self.timer_var.set("Time's up!")
            self.stop_countdown()
            messagebox.showinfo("Hết giờ", "Thời gian của bạn đã hết! Trận đấu kết thúc.")
            self.set_status("You lost (timeout).")
            self.send_json({'type': 'timeout'})

    def stop_countdown(self):
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
        self.timer_var.set('')
        self.deadline = None

    # ================== MESSAGE HANDLING ==================
    def handle_disconnect(self):
        self.set_status('Disconnected')
        self.connect_btn['state'] = 'normal'
        self.disconnect_btn['state'] = 'disabled'
        self.challenge_btn['state'] = 'disabled'
        self.users_listbox.delete(0, tk.END)
        self.clear_board()
        self.disable_board()
        self.chat_area.config(state='normal')
        self.chat_area.delete('1.0', tk.END)
        self.chat_area.config(state='disabled')
        self.stop_countdown()
        self.in_match = False
        self.highlighted = []

    def handle_msg(self, msg):
        t = msg.get('type')
        if t == 'login_ok':
            self.set_status(f'Connected as {self.name}')
            self.connect_btn['state'] = 'disabled'
            self.disconnect_btn['state'] = 'normal'
            self.challenge_btn['state'] = 'normal'
            self.update_users(msg.get('users', []))

        elif t == 'user_list':
            self.update_users(msg.get('users', []))

        elif t == 'invite':
            frm = msg.get('from')
            if messagebox.askyesno('Invite', f'Accept challenge from {frm}?'):
                self.send_json({'type': 'accept', 'opponent': frm})

        elif t == 'match_start':
            self.in_match = True
            self.you = msg.get('you')
            self.opponent = msg.get('opponent')
            self.turn = None
            self.clear_board()
            self.disable_board()
            self.set_status(f'Playing vs {self.opponent} (You: {self.you})')
            self.root.after(100, self.on_canvas_resize)

        elif t == 'your_turn':
            self.turn = self.you
            deadline = msg.get('deadline')
            if deadline:
                self.start_countdown(deadline)
            self.enable_board()
            self.set_status("Your turn!")

        elif t == 'opponent_move' or t == 'move_ok':
            x, y, sym = msg.get('x'), msg.get('y'), msg.get('symbol')
            self.set_cell(x, y, sym)
            self.turn = None
            self.stop_countdown()
            self.disable_board()
            self.set_status("Waiting for opponent...")

        elif t == 'highlight':
            cells = msg.get('cells', [])
            self.highlight_winning_line(cells)
            self.set_status(f"{msg.get('winner', '')} wins!")

        elif t == 'match_end':
            result = msg.get('result')
            if result == 'win':
                messagebox.showinfo("Result", "You win!")
            elif result == 'lose':
                messagebox.showinfo("Result", "You lose!")
            self.clear_board()
            self.disable_board()
            self.stop_countdown()
            self.in_match = False
            self.highlighted = []

        elif t == 'chat':
            sender = msg.get('from')
            text = msg.get('text')
            self.append_chat(f'{sender}: {text}\n')

        elif t == 'error':
            messagebox.showerror('Error', msg.get('msg', ''))

    def update_users(self, users):
        self.users_listbox.delete(0, tk.END)
        for u in users:
            self.users_listbox.insert(tk.END, u)


def main():
    root = tk.Tk()
    app = GuiClient(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.on_disconnect(), root.destroy()))
    root.mainloop()


if __name__ == '__main__':
    main()