import asyncio
import json
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
from queue import Queue, Empty
import time

HOST = "192.168.0.125"
PORT = 7777
BOARD_SIZE = 15

# ============================================
# CÁC HẰNG SỐ - Settings cho UI
# ============================================
RESIZE_DEBOUNCE_MS = 100   # Tăng lên 100ms để tránh lag khi resize
UPDATE_QUEUE_MS = 50       # Giảm xuống 50ms để responsive hơn
RECONNECT_DELAY = 2.0
HEARTBEAT_INTERVAL = 5.0   # Ping server mỗi 5s để check connection

class GuiClient:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('Caro Online - Enhanced')
        self.root.geometry("1200x750")
        self.root.config(bg="#1e1e2f")

        # ============================================
        # TRẠNG THÁI MẠNG + GAME
        # ============================================
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
        self.is_closing = False
        self.last_move_time = 0  # Track để tránh double-click

        # ============================================
        # TRẠNG THÁI BÀN CỜ
        # ============================================
        self.board_state = [['' for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.cell_size = 0
        self.offset_x = 0
        self.offset_y = 0
        self.board_enabled = False  # Flag để track board state
        self.last_move = None  # Lưu vị trí (x, y) của nước đi cuối cùng

        # ========================================
        # HEADER - Thanh trên cùng
        # ========================================
        header = tk.Frame(root, bg="#252539", pady=10)
        header.pack(side='top', fill='x')
        
        tk.Label(header, text='Name:', bg="#252539", fg="white", font=("Segoe UI", 10)).pack(side='left', padx=5)
        self.name_var = tk.StringVar(value='Player')
        self.name_entry = tk.Entry(header, textvariable=self.name_var, width=15, bg="#333347", fg="white", insertbackground="white")
        self.name_entry.pack(side='left', padx=5)
        
        self.connect_btn = tk.Button(header, text='Connect', command=self.on_connect, bg="#0078D7", fg="white", relief='flat', padx=15)
        self.connect_btn.pack(side='left', padx=5)
        self.disconnect_btn = tk.Button(header, text='Disconnect', command=self.on_disconnect, bg="#d9534f", fg="white", relief='flat', state='disabled', padx=15)
        self.disconnect_btn.pack(side='left', padx=5)

        # Connection status indicator
        self.conn_indicator = tk.Label(header, text="●", fg="#888888", bg="#252539", font=("Arial", 16))
        self.conn_indicator.pack(side='right', padx=10)

        # ========================================
        # INFO BAR - Thanh thông tin
        # ========================================
        info_bar = tk.Frame(root, bg="#1e1e2f", height=35)
        info_bar.pack(side='top', fill='x', pady=(0, 5))
        info_bar.pack_propagate(False)

        self.status_var = tk.StringVar(value='Not connected')
        self.status_label = tk.Label(info_bar, textvariable=self.status_var, bg="#1e1e2f",
                                     fg="#FFD700", font=("Segoe UI", 11, "italic"), anchor='w')
        self.status_label.pack(side='left', padx=15, fill='x', expand=True)

        self.timer_var = tk.StringVar(value='')
        self.timer_label = tk.Label(info_bar, textvariable=self.timer_var,
                                    bg="#1e1e2f", fg="#00FFAA", font=("Consolas", 13, "bold"))
        self.timer_label.pack(side='right', padx=20)

        # ========================================
        # LEFT PANEL - Danh sách người chơi
        # ========================================
        left_panel = tk.Frame(root, bg="#2b2b3c", width=200)
        left_panel.pack(side='left', fill='y', padx=(5, 0))
        
        tk.Label(left_panel, text='Online Users', bg="#2b2b3c", fg="#00FFAA", 
                font=("Segoe UI", 12, "bold")).pack(pady=10)
        
        # Frame chứa listbox + scrollbar
        list_frame = tk.Frame(left_panel, bg="#2b2b3c")
        list_frame.pack(fill='both', expand=True, padx=8, pady=(0, 10))
        
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side='right', fill='y')
        
        self.users_listbox = tk.Listbox(list_frame, height=20, bg="#1e1e2f", fg="white", 
                                        selectbackground="#00FFAA", selectforeground="black",
                                        relief='flat', yscrollcommand=scrollbar.set)
        self.users_listbox.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=self.users_listbox.yview)
        
        self.challenge_btn = tk.Button(left_panel, text='Challenge Selected', command=self.on_challenge, 
                                       bg="#00b894", fg="white", relief='flat', state='disabled', 
                                       font=("Segoe UI", 10, "bold"), pady=8)
        self.challenge_btn.pack(pady=10, padx=10, fill='x')

        # ========================================
        # CENTER - Bàn cờ
        # ========================================
        center_frame = tk.Frame(root, bg="#1e1e2f")
        center_frame.pack(side='left', expand=True, fill='both', padx=10, pady=(0, 10))

        self.canvas = tk.Canvas(center_frame, bg="#1e1e2f", highlightthickness=0)
        self.canvas.pack(expand=True, fill='both')
        self.canvas.bind('<Configure>', self.on_canvas_configure)
        
        # Bind click trực tiếp vào canvas
        self.canvas.bind('<Button-1>', self.on_canvas_click)

        # ========================================
        # RIGHT PANEL - Chat box
        # ========================================
        right_panel = tk.Frame(root, bg="#2b2b3c", width=280)
        right_panel.pack(side='right', fill='y', padx=(0, 5))
        
        tk.Label(right_panel, text='Chat', bg="#2b2b3c", fg="#FFD700", 
                font=("Segoe UI", 12, "bold")).pack(pady=8)

        self.chat_area = scrolledtext.ScrolledText(right_panel, width=32, height=30, 
                                                   bg="#1e1e2f", fg="white", wrap='word', 
                                                   state='disabled', relief='flat')
        self.chat_area.pack(padx=10, pady=5, fill='both', expand=True)

        self.chat_area.tag_config("you", foreground="#00FFAA", font=("Segoe UI", 9, "bold"))
        self.chat_area.tag_config("opponent", foreground="#FF6B6B", font=("Segoe UI", 9, "bold"))
        self.chat_area.tag_config("system", foreground="#FFD700", font=("Segoe UI", 9, "italic"))

        chat_entry_frame = tk.Frame(right_panel, bg="#2b2b3c")
        chat_entry_frame.pack(fill='x', padx=10, pady=(0, 10))
        self.chat_entry = tk.Entry(chat_entry_frame, bg="#333347", fg="white", 
                                   relief='flat', font=("Segoe UI", 9))
        self.chat_entry.pack(side='left', fill='x', expand=True, padx=(0, 5))
        self.chat_entry.bind('<Return>', self.on_send_chat)
        tk.Button(chat_entry_frame, text='Send', command=self.on_send_chat, 
                 bg="#00AEEF", fg="white", relief='flat', padx=10).pack(side='right')

        # Vẽ board lần đầu
        self.root.after(200, self.on_canvas_resize)
        
        # Bắt đầu vòng lặp xử lý queue
        self.root.after(UPDATE_QUEUE_MS, self.process_queue)

    # =====================================
    # VẼ BÀN CỜ 3D
    # =====================================
    
    def on_canvas_configure(self, event=None):
        """Debounce resize để tránh lag"""
        if self.resize_debounce:
            self.root.after_cancel(self.resize_debounce)
        self.resize_debounce = self.root.after(RESIZE_DEBOUNCE_MS, self.on_canvas_resize)

    def on_canvas_resize(self):
        """Vẽ lại toàn bộ bàn cờ"""
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return

        # Tính cell size với padding
        padding = 40
        available_width = width - padding * 2
        available_height = height - padding * 2
        self.cell_size = min(available_width, available_height) // BOARD_SIZE
        
        if self.cell_size < 10:
            return

        self.canvas.delete('all')

        # Tính offset để center
        board_width = self.cell_size * BOARD_SIZE
        board_height = self.cell_size * BOARD_SIZE
        self.offset_x = (width - board_width) // 2
        self.offset_y = (height - board_height) // 2

        # Vẽ background cho board
        self.canvas.create_rectangle(
            self.offset_x - 10, self.offset_y - 10,
            self.offset_x + board_width + 10, self.offset_y + board_height + 10,
            fill="#252539", outline="#3a3a50", width=2
        )

        # Vẽ lưới
        for i in range(BOARD_SIZE + 1):
            # Vertical lines
            x = self.offset_x + i * self.cell_size
            self.canvas.create_line(x, self.offset_y, x, self.offset_y + board_height,
                                   fill="#3a3a50", width=1)
            # Horizontal lines
            y = self.offset_y + i * self.cell_size
            self.canvas.create_line(self.offset_x, y, self.offset_x + board_width, y,
                                   fill="#3a3a50", width=1)

        # Vẽ các điểm đánh dấu (star points)
        star_points = [(3, 3), (3, 11), (7, 7), (11, 3), (11, 11)]
        for sx, sy in star_points:
            cx = self.offset_x + sx * self.cell_size + self.cell_size // 2
            cy = self.offset_y + sy * self.cell_size + self.cell_size // 2
            r = max(3, self.cell_size // 15)
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, 
                                   fill="#5a5a70", outline="")

        # Vẽ lại các quân cờ
        self.redraw_board_from_state()

    def draw_3d_cell(self, x, y, base_color="#2b2c3c", symbol='', text_color="#FFFFFF"):
        """Vẽ 1 ô với hiệu ứng 3D"""
        if self.cell_size < 10:
            return

        x1 = self.offset_x + x * self.cell_size
        y1 = self.offset_y + y * self.cell_size
        x2 = x1 + self.cell_size
        y2 = y1 + self.cell_size

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        tag = f"cell_{x}_{y}"
        self.canvas.delete(tag)

        if symbol:
            # Vẽ quân cờ dạng hình tròn với gradient effect
            radius = int(self.cell_size * 0.35)
            shadow_offset = max(2, radius // 10)

            # Shadow
            self.canvas.create_oval(
                cx - radius + shadow_offset, cy - radius + shadow_offset,
                cx + radius + shadow_offset, cy + radius + shadow_offset,
                fill="#000000", outline="", tags=tag
            )

            # Main circle
            self.canvas.create_oval(
                cx - radius, cy - radius, cx + radius, cy + radius,
                fill=base_color, outline="", tags=tag
            )

            # Symbol text
            font_size = max(12, int(self.cell_size * 0.4))
            self.canvas.create_text(
                cx + 1, cy + 1,
                text=symbol, font=("Consolas", font_size, "bold"),
                fill="#000000", tags=tag
            )
            self.canvas.create_text(
                cx, cy,
                text=symbol, font=("Consolas", font_size, "bold"),
                fill=text_color, tags=tag
            )

    def redraw_board_from_state(self):
        """Vẽ lại toàn bộ board từ state"""
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                symbol = self.board_state[y][x]
                if symbol == "X":
                    self.draw_3d_cell(x, y, "#FF3B30", "X", "#FFFFFF")
                elif symbol == "O":
                    self.draw_3d_cell(x, y, "#0078D7", "O", "#FFFFFF")

        self.draw_highlights()

    def draw_highlights(self):
        """Vẽ highlight cho nước đi cuối cùng và line thắng"""
        self.canvas.delete("highlight")
        
        # Highlight nước đi cuối cùng (nếu có)
        if self.last_move:
            x, y = self.last_move
            if 0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE:
                x1 = self.offset_x + x * self.cell_size
                y1 = self.offset_y + y * self.cell_size
                x2 = x1 + self.cell_size
                y2 = y1 + self.cell_size
                
                # Cyan highlight cho nước đi cuối cùng
                for i in range(2):
                    offset = 2 + i * 2
                    width = 3 - i
                    self.canvas.create_rectangle(
                        x1 - offset, y1 - offset, x2 + offset, y2 + offset,
                        outline="#00FFFF", width=width, tags="highlight"
                    )
        
        # Highlight line thắng (nếu có)
        if self.highlighted:
            for (x, y) in self.highlighted:
                if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
                    continue
                
                x1 = self.offset_x + x * self.cell_size
                y1 = self.offset_y + y * self.cell_size
                x2 = x1 + self.cell_size
                y2 = y1 + self.cell_size

                # Gold glow effect cho line thắng
                for i in range(3):
                    offset = 3 + i * 2
                    width = 4 - i
                    self.canvas.create_rectangle(
                        x1 - offset, y1 - offset, x2 + offset, y2 + offset,
                        outline="#FFD700", width=width, tags="highlight"
                    )

    def set_cell(self, x, y, symbol):
        """Đặt quân cờ vào ô"""
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return
        
        self.board_state[y][x] = symbol
        base_color = "#FF3B30" if symbol == "X" else "#0078D7"
        self.draw_3d_cell(x, y, base_color, symbol, "#FFFFFF")
        self.draw_highlights()

    def clear_board(self):
        """Xóa sạch bàn cờ"""
        self.board_state = [['' for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.highlighted = []
        self.last_move = None
        if self.cell_size > 0:
            self.on_canvas_resize()

    def highlight_winning_line(self, cells):
        """Highlight các ô thắng"""
        self.highlighted = cells
        self.redraw_board_from_state()

    def enable_board(self):
        """Bật tương tác với board"""
        self.board_enabled = True
        self.canvas.config(cursor="hand2")

    def disable_board(self):
        """Tắt tương tác"""
        self.board_enabled = False
        self.canvas.config(cursor="")

    def on_canvas_click(self, event):
        """Xử lý click vào canvas"""
        # Check conditions
        if not self.board_enabled or not self.in_match or self.you != self.turn:
            return

        # Debounce double-click
        now = time.time()
        if now - self.last_move_time < 0.3:
            return
        self.last_move_time = now

        # Convert pixel to board coordinates
        if self.cell_size <= 0:
            return

        x = (event.x - self.offset_x) // self.cell_size
        y = (event.y - self.offset_y) // self.cell_size

        # Validate coordinates
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return

        # Check if cell is empty
        if self.board_state[y][x]:
            self.append_chat("That cell is occupied!\n", "system")
            return

        # Send move
        self.disable_board()
        self.send_json({'type': 'move', 'x': x, 'y': y})
        self.append_chat(f'Playing at ({x}, {y})...\n', "you")

    # =====================================
    # MẠNG - Kết nối với server
    # =====================================
    
    def on_connect(self):
        """Người dùng nhấn Connect"""
        if self.writer:
            messagebox.showinfo('Info', 'Already connected')
            return
        
        # Validate tên
        self.name = self.name_var.get().strip()
        if not self.name:
            self.name = "Player"
            self.name_var.set("Player")
        elif len(self.name) > 50:
            messagebox.showerror('Error', 'Name too long (max 50 characters)')
            return
        
        # Disable name entry
        self.name_entry.config(state='disabled')
        
        self.set_status('Connecting...')
        self.connect_btn['state'] = 'disabled'
        
        # Start async thread
        threading.Thread(target=self.start_async_loop, daemon=True).start()

    def on_disconnect(self):
        """Người dùng nhấn Disconnect"""
        if not self.writer:
            return
        self.set_status('Disconnecting...')
        self.is_closing = True
        if self.loop:
            self.loop.call_soon_threadsafe(self._close_connection)

    def _close_connection(self):
        """Đóng connection"""
        if self.writer and not self.writer.is_closing():
            self.writer.close()

    def on_challenge(self):
        """Người dùng nhấn Challenge"""
        sel = self.users_listbox.curselection()
        if not sel:
            messagebox.showinfo('Info', 'Select a user to challenge')
            return
        
        opponent = self.users_listbox.get(sel[0])
        if opponent == self.name:
            messagebox.showinfo('Info', 'Cannot challenge yourself')
            return
        
        self.challenge_btn['state'] = 'disabled'
        self.send_json({'type': 'challenge', 'opponent': opponent})
        self.append_chat(f'Challenge sent to {opponent}...\n', "system")

    def on_send_chat(self, event=None):
        """Người dùng gửi chat"""
        text = self.chat_entry.get().strip()
        if not text:
            return
        
        if len(text) > 500:
            messagebox.showwarning('Warning', 'Message too long (max 500 characters)')
            return
        
        if self.in_match:
            self.send_json({'type': 'chat', 'text': text})
            self.append_chat(f'You: {text}\n', "you")
        else:
            self.append_chat(f'(Not in match) {text}\n', "system")
        
        self.chat_entry.delete(0, tk.END)

    def start_async_loop(self):
        """Chạy trong thread riêng"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.async_main())
        except Exception as e:
            print(f"[ERROR] Async loop error: {e}")
            if not self.is_closing:
                self.queue.put((self.handle_disconnect, ()))
        finally:
            if self.loop:
                pending = asyncio.all_tasks(self.loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self.loop.close()
            self.loop = None
            self.writer = None
            self.reader = None

    async def async_main(self):
        """Hàm chính của async thread"""
        try:
            self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
            self.queue.put((self.update_connection_indicator, (True,)))
        except Exception as e:
            self.queue.put((self.set_status, (f'Connect failed: {e}',)))
            self.queue.put((self.handle_disconnect, ()))
            return

        # Gửi login
        await self.send_json_async({'type': 'login', 'name': self.name})

        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                
                msg = json.loads(line.decode('utf-8').strip())
                self.queue.put((self.handle_msg, (msg,)))
                
        except asyncio.CancelledError:
            print("[INFO] Connection cancelled")
        except Exception as e:
            if not self.is_closing:
                print(f"[ERROR] Connection error: {e}")
        finally:
            self.queue.put((self.handle_disconnect, ()))
            if self.writer:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except:
                    pass
                self.writer = None
            self.reader = None

    async def send_json_async(self, obj):
        """Gửi JSON lên server"""
        if not self.writer or self.writer.is_closing():
            return
        try:
            data = json.dumps(obj, ensure_ascii=False) + '\n'
            self.writer.write(data.encode('utf-8'))
            await self.writer.drain()
        except Exception as e:
            print(f"[ERROR] Send failed: {e}")

    def send_json(self, obj):
        """Gửi JSON từ main thread"""
        if not self.writer or not self.loop:
            return
        if self.writer.is_closing():
            return
        asyncio.run_coroutine_threadsafe(self.send_json_async(obj), self.loop)

    # =====================================
    # UI HELPERS
    # =====================================
    
    def process_queue(self):
        """Xử lý queue message từ async thread"""
        try:
            while True:
                fn, args = self.queue.get_nowait()
                fn(*args)
        except Empty:
            pass
        self.root.after(UPDATE_QUEUE_MS, self.process_queue)

    def set_status(self, text):
        """Cập nhật status label"""
        self.status_var.set(text)

    def update_connection_indicator(self, connected):
        """Cập nhật indicator kết nối"""
        if connected:
            self.conn_indicator.config(fg="#00FF00")  # Green
        else:
            self.conn_indicator.config(fg="#888888")  # Gray

    def append_chat(self, text, tag=None):
        """Thêm text vào chat area"""
        self.chat_area.config(state='normal')
        if tag:
            self.chat_area.insert(tk.END, text, (tag,))
        else:
            self.chat_area.insert(tk.END, text)
        self.chat_area.config(state='disabled')
        self.chat_area.see(tk.END)

    # =====================================
    # COUNTDOWN TIMER
    # =====================================
    
    def start_countdown(self, deadline):
        """Bắt đầu đếm ngược
        
        Args:
            deadline: Unix timestamp khi hết giờ (từ server)
        """
        if not deadline or deadline <= 0:
            return
        
        # 🔴 QUAN TRỌNG: Dừng timer cũ
        self.stop_countdown()
        
        # Lưu deadline tuyệt đối từ server (không cộng thêm)
        self.deadline = deadline
        self.timer_label.config(fg="#00FFAA")
        self.update_timer()

    def update_timer(self):
        """Cập nhật timer mỗi giây"""
        if not self.deadline:
            self.timer_var.set('')
            self.timer_id = None
            return

        # 🔴 QUAN TRỌNG: Tính remaining dựa vào deadline tuyệt đối
        remaining = int(self.deadline - time.time())
        
        if remaining > 0:
            # Chọn màu dựa vào thời gian còn lại
            if remaining <= 5:
                self.timer_label.config(fg="#FF3B30")  # Red
            elif remaining <= 10:
                self.timer_label.config(fg="#FFA500")  # Orange
            else:
                self.timer_label.config(fg="#00FFAA")  # Green
            
            self.timer_var.set(f"⏱ {remaining}s")
            # Schedule update tiếp theo
            self.timer_id = self.root.after(1000, self.update_timer)
        else:
            # Hết giờ
            self.timer_var.set("⏱ Time's up!")
            self.timer_label.config(fg="#FF3B30")
            self.append_chat("⚠️ Your time expired!\n", "system")
            self.send_json({'type': 'timeout'})
            
            # Reset state
            self.deadline = None
            self.timer_id = None

    def stop_countdown(self):
        """Dừng timer"""
        if self.timer_id is not None:
            try:
                self.root.after_cancel(self.timer_id)
            except tk.TclError:
                pass
        
        self.timer_var.set('')
        self.deadline = None
        self.timer_id = None
        self.timer_label.config(fg="#00FFAA")

    # =====================================
    # XỬ LÝ MESSAGE TỪ SERVER
    # =====================================
    
    def handle_disconnect(self):
        """Xử lý khi disconnect"""
        self.set_status('❌ Disconnected')
        self.update_connection_indicator(False)
        self.connect_btn['state'] = 'normal'
        self.disconnect_btn['state'] = 'disabled'
        self.challenge_btn['state'] = 'disabled'
        self.name_entry.config(state='normal')
        self.users_listbox.delete(0, tk.END)
        self.clear_board()
        self.disable_board()
        self.stop_countdown()
        self.in_match = False
        self.highlighted = []
        self.is_closing = False

    def handle_msg(self, msg):
        """XỬ LÝ TẤT CẢ MESSAGE TỪ SERVER"""
        t = msg.get('type')
        
        if t == 'login_ok':
            self.set_status(f'✅ Connected as {self.name}')
            self.connect_btn['state'] = 'disabled'
            self.disconnect_btn['state'] = 'normal'
            self.challenge_btn['state'] = 'normal'
            self.update_users(msg.get('users', []))
            self.append_chat('╔════════════════════════╗\n', "system")
            self.append_chat('║  Connected to server  ║\n', "system")
            self.append_chat('╚════════════════════════╝\n', "system")

        elif t == 'user_list':
            self.update_users(msg.get('users', []))

        elif t == 'challenge_sent':
            to = msg.get('to')
            self.append_chat(f'⏳ Waiting for {to} to accept...\n', "system")
            self.challenge_btn['state'] = 'normal'

        elif t == 'invite':
            frm = msg.get('from')
            if messagebox.askyesno('Challenge Request', 
                                   f'🎮 {frm} challenges you!\n\nAccept the challenge?'):
                self.send_json({'type': 'accept', 'opponent': frm})
                self.append_chat(f'✅ Accepted challenge from {frm}\n', "system")
            else:
                self.append_chat(f'❌ Declined challenge from {frm}\n', "system")

        elif t == 'match_start':
            self.in_match = True
            self.you = msg.get('you')
            self.opponent = msg.get('opponent')
            self.turn = None
            self.clear_board()
            self.disable_board()
            self.set_status(f'⚔️ Playing vs {self.opponent} (You: {self.you})')
            
            opp_symbol = "O" if self.you == "X" else "X"
            self.append_chat('\n╔════════════════════════╗\n', "system")
            self.append_chat(f'║   MATCH STARTED!      ║\n', "system")
            self.append_chat('╚════════════════════════╝\n', "system")
            self.append_chat(f'You ({self.you}) vs {self.opponent} ({opp_symbol})\n', "system")
            
            self.root.after(100, self.on_canvas_resize)

        elif t == 'your_turn':
            self.turn = self.you
    
            # 🔴 QUAN TRỌNG: deadline từ server phải là Unix timestamp
            # Server nên gửi: {'type': 'your_turn', 'deadline': time.time() + 30}
            deadline = msg.get('deadline')
            if deadline:
                self.start_countdown(deadline)
    
            self.enable_board()
            self.set_status(f"🎯 Your turn! ({self.you})")
            self.append_chat('▶️ Your turn!\n', "system")

        elif t == 'opponent_move' or t == 'move_ok':
            x, y, sym = msg.get('x'), msg.get('y'), msg.get('symbol')
            
            # Validate coordinates từ server
            if x is None or y is None or sym is None:
                print(f"[ERROR] Invalid move data: {msg}")
                return
            
            try:
                x, y = int(x), int(y)
                if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
                    print(f"[ERROR] Coordinates out of range: ({x}, {y})")
                    return
            except (TypeError, ValueError) as e:
                print(f"[ERROR] Invalid coordinate format: {e}")
                return
            
            self.set_cell(x, y, sym)
            self.last_move = (x, y)  # Lưu vị trí nước đi cuối cùng
            self.turn = None
            self.stop_countdown()
            self.disable_board()
            self.draw_highlights()  # Vẽ highlight
            
            if t == 'opponent_move':
                self.set_status(f"⏸️ {self.opponent} played ({x}, {y})")
                self.append_chat(f'🔵 {self.opponent} played at ({x}, {y})\n', "opponent")
            else:
                self.append_chat(f'✓ Move confirmed at ({x}, {y})\n', "you")

        elif t == 'highlight':
            cells = msg.get('cells', [])
            winner_name = msg.get('winner', '')
            
            # Validate cells data
            validated_cells = []
            for cell in cells:
                try:
                    if isinstance(cell, (list, tuple)) and len(cell) == 2:
                        cx, cy = int(cell[0]), int(cell[1])
                        if 0 <= cx < BOARD_SIZE and 0 <= cy < BOARD_SIZE:
                            validated_cells.append((cx, cy))
                except (TypeError, ValueError):
                    print(f"[ERROR] Invalid cell format: {cell}")
            
            if validated_cells:
                self.highlight_winning_line(validated_cells)
            
            if winner_name == self.name:
                self.set_status("🏆 You win!")
            else:
                self.set_status(f"😢 {winner_name} wins!")

        elif t == 'match_end':
            result = msg.get('result')
            reason = msg.get('reason', '')
            
            # Emoji mapping
            emoji_map = {
                'win': '🎉',
                'lose': '😢',
                'draw': '🤝'
            }
            emoji = emoji_map.get(result, '📊')
            
            if result == 'win':
                title = "🏆 VICTORY!"
                msg_text = f"{emoji} You won!"
                if reason == 'timeout':
                    msg_text += " (opponent timeout)"
                elif reason == 'disconnect':
                    msg_text += " (opponent disconnected)"
                self.append_chat(f'\n{msg_text}\n', "system")
                messagebox.showinfo(title, msg_text)
            elif result == 'lose':
                title = "💔 DEFEAT"
                msg_text = f"{emoji} You lost"
                if reason == 'timeout':
                    msg_text += " (timeout)"
                elif reason == 'disconnect':
                    msg_text += " (disconnected)"
                self.append_chat(f'\n{msg_text}\n', "system")
                messagebox.showinfo(title, msg_text)
            elif result == 'draw':
                msg_text = f"{emoji} It's a draw!"
                self.append_chat(f'\n{msg_text}\n', "system")
                messagebox.showinfo("Draw", msg_text)
            
            # Reset state
            self.clear_board()
            self.disable_board()
            self.stop_countdown()
            self.in_match = False
            self.highlighted = []
            self.set_status('Match ended')
            
            # Re-enable challenge button
            self.challenge_btn['state'] = 'normal'

        elif t == 'chat':
            sender = msg.get('from')
            text = msg.get('text', '')
            if sender and text:
                # Sanitize text
                text = text.strip()[:500]
                tag = "opponent" if sender == self.opponent else None
                self.append_chat(f'{sender}: {text}\n', tag)

        elif t == 'error':
            error_msg = msg.get('msg', 'Unknown error')
            self.append_chat(f'⚠️ Error: {error_msg}\n', "system")
            
            # Hiện popup cho lỗi quan trọng
            critical_keywords = ['name', 'login', 'match', 'connection']
            if any(keyword in error_msg.lower() for keyword in critical_keywords):
                messagebox.showerror('Error', error_msg)
            
            # Re-enable buttons nếu không trong trận
            if not self.in_match:
                self.challenge_btn['state'] = 'normal'
            
            # Re-enable board nếu lỗi không nghiêm trọng và đến lượt mình
            if self.in_match and self.you == self.turn and 'occupied' in error_msg.lower():
                self.enable_board()

    def update_users(self, users):
        """Cập nhật danh sách người online"""
        # Lưu lại selection
        current_selection = None
        if self.users_listbox.curselection():
            try:
                current_selection = self.users_listbox.get(self.users_listbox.curselection()[0])
            except:
                pass
        
        # Clear và rebuild
        self.users_listbox.delete(0, tk.END)
        new_index = None
        
        for i, u in enumerate(users):
            display_text = u
            # Highlight tên mình
            if u == self.name:
                display_text = f"{u} (You)"
            # Highlight opponent nếu đang đấu
            elif u == self.opponent:
                display_text = f"{u} ⚔️"
            
            self.users_listbox.insert(tk.END, u)  # Insert tên gốc để dễ xử lý
            
            if u == current_selection:
                new_index = i
        
        # Restore selection
        if new_index is not None:
            self.users_listbox.selection_set(new_index)
            self.users_listbox.see(new_index)


def main():
    """
    ENTRY POINT
    """
    root = tk.Tk()
    app = GuiClient(root)
    
    def on_closing():
        """Xử lý khi đóng cửa sổ"""
        if app.in_match:
            if not messagebox.askokcancel("Quit", 
                                         "⚠️ You are in a match!\n\nQuitting will result in a loss.\n\nAre you sure?"):
                return
        elif app.writer:
            if not messagebox.askokcancel("Quit", "Disconnect and quit?"):
                return
        
        app.on_disconnect()
        root.after(300, root.destroy)
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Set minimum window size
    root.minsize(900, 600)
    
    # Center window on screen
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f'{width}x{height}+{x}+{y}')
    
    root.mainloop()


if __name__ == '__main__':
    main()
