import asyncio
import json
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
from queue import Queue, Empty
import time

HOST = "127.0.0.1"
PORT = 7777
BOARD_SIZE = 15

# ============================================
# CÁC HẰNG SỐ - Settings cho UI
# ============================================
RESIZE_DEBOUNCE_MS = 50   # Đợi 50ms sau khi resize mới vẽ lại (chống lag)
UPDATE_QUEUE_MS = 100     # Cứ 100ms check 1 lần có message từ server không
RECONNECT_DELAY = 2.0     # Đợi 2 giây trước khi reconnect


class GuiClient:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('Caro Online')
        self.root.geometry("1100x700")
        self.root.config(bg="#1e1e2f")  # Dark theme

        # ============================================
        # TRẠNG THÁI MẠNG + GAME
        # ============================================
        self.queue = Queue()  # Hàng đợi message từ async thread
        self.reader = None    # Ống đọc data từ server
        self.writer = None    # Ống ghi data lên server
        self.loop = None      # Event loop của asyncio
        self.name = ''        # Tên người chơi
        self.in_match = False # Đang trong trận không?
        self.you = None       # Bạn cầm X hay O?
        self.opponent = None  # Tên đối thủ
        self.turn = None      # Lượt của ai?
        self.deadline = None  # Hết giờ lúc nào?
        self.timer_id = None  # ID của timer đang chạy
        self.highlighted = [] # Các ô được highlight (line thắng)
        self.resize_debounce = None  # ID để cancel resize cũ
        self.is_closing = False  # Đang tắt app không?

        # ============================================
        # TRẠNG THÁI BÀN CỜ
        # ============================================
        # board_state[y][x] = '' | 'X' | 'O'
        self.board_state = [['' for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.cell_size = 0   # Kích thước 1 ô (tính động theo canvas)
        self.offset_x = 0    # Lề trái để center board
        self.offset_y = 0    # Lề trên để center board

        # ========================================
        # HEADER - Thanh trên cùng
        # ========================================
        header = tk.Frame(root, bg="#252539", pady=10)
        header.pack(side='top', fill='x')
        
        # Input tên
        tk.Label(header, text='Name:', bg="#252539", fg="white").pack(side='left', padx=5)
        self.name_var = tk.StringVar(value='Player')
        tk.Entry(header, textvariable=self.name_var, width=15, bg="#333347", fg="white", insertbackground="white").pack(side='left', padx=5)
        
        # Nút Connect/Disconnect
        self.connect_btn = tk.Button(header, text='Connect', command=self.on_connect, bg="#0078D7", fg="white", relief='flat')
        self.connect_btn.pack(side='left', padx=5)
        self.disconnect_btn = tk.Button(header, text='Disconnect', command=self.on_disconnect, bg="#d9534f", fg="white", relief='flat', state='disabled')
        self.disconnect_btn.pack(side='left', padx=5)

        # ========================================
        # INFO BAR - Thanh thông tin
        # ========================================
        info_bar = tk.Frame(root, bg="#1e1e2f", height=30)
        info_bar.pack(side='top', fill='x', pady=(0, 5))
        info_bar.pack_propagate(False)

        # Status text (trái)
        self.status_var = tk.StringVar(value='Not connected')
        self.status_label = tk.Label(info_bar, textvariable=self.status_var, bg="#1e1e2f",
                                     fg="#FFD700", font=("Segoe UI", 10, "italic"), anchor='w')
        self.status_label.pack(side='left', padx=15, fill='x', expand=True)

        # Timer (phải)
        self.timer_var = tk.StringVar(value='')
        self.timer_label = tk.Label(info_bar, textvariable=self.timer_var,
                                    bg="#1e1e2f", fg="#00FFAA", font=("Consolas", 12, "bold"))
        self.timer_label.pack(side='right', padx=20)

        # ========================================
        # LEFT PANEL - Danh sách người chơi
        # ========================================
        left_panel = tk.Frame(root, bg="#2b2b3c", width=180)
        left_panel.pack(side='left', fill='y')
        
        tk.Label(left_panel, text='Online Users', bg="#2b2b3c", fg="#00FFAA", font=("Segoe UI", 12, "bold")).pack(pady=10)
        
        # Listbox hiển thị người online
        self.users_listbox = tk.Listbox(left_panel, height=15, bg="#1e1e2f", fg="white", selectbackground="#00FFAA", relief='flat')
        self.users_listbox.pack(fill='y', padx=8)
        
        # Nút thách đấu
        self.challenge_btn = tk.Button(left_panel, text='Challenge', command=self.on_challenge, bg="#00b894", fg="white", relief='flat', state='disabled')
        self.challenge_btn.pack(pady=10)

        # ========================================
        # CENTER - Bàn cờ
        # ========================================
        center_frame = tk.Frame(root, bg="#1e1e2f")
        center_frame.pack(side='left', expand=True, fill='both', padx=10, pady=(0, 10))

        # Canvas để vẽ bàn cờ
        self.canvas = tk.Canvas(center_frame, bg="#1e1e2f", highlightthickness=0)
        self.canvas.pack(expand=True, fill='both')

        # Bind sự kiện resize
        self.canvas.bind('<Configure>', self.on_canvas_configure)
        self.root.after(200, self.on_canvas_resize)

        # ========================================
        # RIGHT PANEL - Chat box
        # ========================================
        right_panel = tk.Frame(root, bg="#2b2b3c", width=250)
        right_panel.pack(side='right', fill='y')
        
        tk.Label(right_panel, text='Chat', bg="#2b2b3c", fg="#FFD700", font=("Segoe UI", 12, "bold")).pack(pady=5)

        # ScrolledText để hiển thị chat
        self.chat_area = scrolledtext.ScrolledText(right_panel, width=30, height=25, bg="#1e1e2f", fg="white", wrap='word', state='disabled')
        self.chat_area.pack(padx=10, pady=5, fill='both', expand=True)

        # Configure tags cho màu chữ
        self.chat_area.tag_config("you", foreground="#00FFAA")      # Tin nhắn của bạn = xanh lá
        self.chat_area.tag_config("system", foreground="#FFD700", font=("Segoe UI", 9, "italic"))  # System = vàng

        # Input chat
        chat_entry_frame = tk.Frame(right_panel, bg="#2b2b3c")
        chat_entry_frame.pack(fill='x', padx=10, pady=5)
        self.chat_entry = tk.Entry(chat_entry_frame, bg="#333347", fg="white", relief='flat')
        self.chat_entry.pack(side='left', fill='x', expand=True, padx=(0, 5))
        self.chat_entry.bind('<Return>', self.on_send_chat)  # Enter để gửi
        tk.Button(chat_entry_frame, text='Send', command=self.on_send_chat, bg="#00AEEF", fg="white", relief='flat').pack(side='right')

        # Bắt đầu vòng lặp xử lý queue
        self.root.after(UPDATE_QUEUE_MS, self.process_queue)

    # =====================================
    # VẼ BÀN CỜ 3D - Phần visual đẹp mắt
    # =====================================
    
    def on_canvas_configure(self, event=None):
        """
        Khi canvas bị resize (cửa sổ to/nhỏ)
        Dùng debounce để không vẽ lại liên tục (gây lag)
        """
        if self.resize_debounce:
            self.root.after_cancel(self.resize_debounce)  # Hủy lệnh vẽ cũ
        # Đợi 50ms nữa mới vẽ (nếu resize tiếp thì lại đợi)
        self.resize_debounce = self.root.after(RESIZE_DEBOUNCE_MS, self.on_canvas_resize)

    def on_canvas_resize(self):
        """
        Vẽ lại toàn bộ bàn cờ khi resize
        Tính toán cell_size và offset để center board
        """
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return

        # Cell size = min(width, height) / 15
        self.cell_size = min(width, height) // BOARD_SIZE
        self.canvas.delete('all')  # Xóa tất cả

        # Tính offset để center board
        self.offset_x = (width - self.cell_size * BOARD_SIZE) // 2
        self.offset_y = (height - self.cell_size * BOARD_SIZE) // 2

        # Vẽ lưới 15x15
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                x1 = self.offset_x + x * self.cell_size
                y1 = self.offset_y + y * self.cell_size
                x2 = x1 + self.cell_size
                y2 = y1 + self.cell_size
                self.canvas.create_rectangle(x1, y1, x2, y2, outline="#3a3a50", width=1, fill="")

        # Vẽ lại các quân cờ từ state
        self.redraw_board_from_state()

    def draw_3d_cell(self, x, y, base_color="#2b2b3c", symbol='', text_color="#FFFFFF"):
        """
        Vẽ 1 ô với hiệu ứng 3D
        - Shadow: bóng đổ phía dưới-phải
        - Light edges: viền sáng phía trên-trái
        - Symbol: X hoặc O với shadow
        """
        if self.cell_size < 10:
            return

        # Tính tọa độ ô
        x1 = self.offset_x + x * self.cell_size
        y1 = self.offset_y + y * self.cell_size
        x2 = x1 + self.cell_size
        y2 = y1 + self.cell_size

        # Tính offset cho hiệu ứng 3D
        shadow_offset = max(2, self.cell_size // 15)
        light_offset = max(1, self.cell_size // 20)

        tag = f"cell_3d_{x}_{y}"
        self.canvas.delete(tag)

        # 1. Vẽ bóng đổ (shadow)
        self.canvas.create_rectangle(
            x1 + shadow_offset, y1 + shadow_offset, x2 + shadow_offset, y2 + shadow_offset,
            fill="#1a1a2e", outline="", tags=tag
        )

        # 2. Vẽ ô chính
        self.canvas.create_rectangle(
            x1, y1, x2, y2,
            fill=base_color, outline="", tags=tag
        )

        # 3. Vẽ viền sáng (light edges) - TOP + LEFT
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

        # 4. Vẽ symbol (X hoặc O) với shadow
        if symbol:
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            font_size = max(14, self.cell_size // 3)

            # Shadow của text
            self.canvas.create_text(
                center_x + 1, center_y + 1,
                text=symbol, font=("Consolas", font_size, "bold"),
                fill="#000000", tags=tag
            )
            # Text chính
            self.canvas.create_text(
                center_x, center_y,
                text=symbol, font=("Consolas", font_size, "bold"),
                fill=text_color, tags=tag
            )

        # 5. Bind click cho ô trống (để đánh)
        if not symbol:
            self.canvas.tag_bind(tag, '<Button-1>', lambda e, xx=x, yy=y: self.on_cell(xx, yy))

    def redraw_board_from_state(self):
        """
        Vẽ lại toàn bộ board từ self.board_state
        Dùng khi resize hoặc cần refresh UI
        """
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                symbol = self.board_state[y][x]
                base_color = "#2b2b3c"  # Màu nền mặc định
                text_color = "#FFFFFF"

                # Ô có X -> nền xanh dương
                if symbol == "X":
                    base_color = "#0078D7"
                # Ô có O -> nền đỏ
                elif symbol == "O":
                    base_color = "#FF3B30"

                self.draw_3d_cell(x, y, base_color, symbol, text_color)

        # Vẽ highlight (line thắng) nếu có
        self.draw_highlights()

    def draw_highlights(self):
        """
        Vẽ viền vàng cho các ô trong line thắng
        2 layer: outer (vàng) + inner (trắng)
        """
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

            # Viền ngoài màu vàng
            outer = self.canvas.create_rectangle(
                x1 - 4, y1 - 4, x2 + 4, y2 + 4,
                outline="#FFD700", width=5, tags="highlight"
            )
            # Viền trong màu trắng
            inner = self.canvas.create_rectangle(
                x1 - 2, y1 - 2, x2 + 2, y2 + 2,
                outline="#FFFFFF", width=2, tags="highlight"
            )
            # Đưa lên trên cùng
            self.canvas.tag_raise(outer)
            self.canvas.tag_raise(inner)

    def set_cell(self, x, y, symbol):
        """
        Đặt quân cờ vào ô (x, y)
        Cập nhật state + vẽ lại ô đó
        """
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return
        
        self.board_state[y][x] = symbol
        base_color = "#0078D7" if symbol == "X" else "#FF3B30"
        self.draw_3d_cell(x, y, base_color, symbol, "#FFFFFF")
        self.draw_highlights()

    def clear_board(self):
        """Xóa sạch bàn cờ - reset về trạng thái ban đầu"""
        self.board_state = [['' for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.highlighted = []
        self.canvas.delete('all')
        if self.cell_size > 0:
            self.on_canvas_resize()

    def highlight_winning_line(self, cells):
        """Highlight các ô thắng"""
        self.highlighted = cells
        self.redraw_board_from_state()

    def enable_board(self):
        """
        Bật tương tác với board (đến lượt bạn)
        Bind click cho tất cả ô trống
        """
        if not self.in_match or self.you != self.turn:
            return
        
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                if not self.board_state[y][x]:
                    tag = f"cell_3d_{x}_{y}"
                    self.canvas.tag_bind(tag, '<Button-1>', lambda e, xx=x, yy=y: self.on_cell(xx, yy))

    def disable_board(self):
        """
        Tắt tương tác (không phải lượt bạn)
        Unbind tất cả click
        """
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                if not self.board_state[y][x]:
                    tag = f"cell_3d_{x}_{y}"
                    try:
                        self.canvas.tag_unbind(tag, '<Button-1>')
                    except:
                        pass

    # =====================================
    # MẠNG - Kết nối với server
    # =====================================
    
    def on_connect(self):
        """
        Người dùng nhấn nút Connect
        Validate tên -> Tạo thread mới chạy asyncio
        """
        if self.writer:
            messagebox.showinfo('Info', 'Already connected')
            return
        
        # Validate tên (1-50 ký tự)
        self.name = self.name_var.get().strip()
        if not self.name:
            self.name = "Player"
            self.name_var.set("Player")
        elif len(self.name) > 50:
            messagebox.showerror('Error', 'Name too long (max 50 characters)')
            return
        
        # Đổi UI
        self.set_status('Connecting...')
        self.connect_btn['state'] = 'disabled'
        
        # Tạo thread chạy asyncio (không block UI)
        threading.Thread(target=self.start_async_loop, daemon=True).start()

    def on_disconnect(self):
        """
        Người dùng nhấn Disconnect
        Đánh dấu is_closing -> đóng connection
        """
        if not self.writer:
            return
        self.set_status('Disconnecting...')
        self.is_closing = True  # Đánh dấu disconnect chủ động
        if self.loop:
            # Gọi _close_connection trong event loop
            self.loop.call_soon_threadsafe(self._close_connection)

    def _close_connection(self):
        """Đóng connection (gọi từ event loop)"""
        if self.writer and not self.writer.is_closing():
            self.writer.close()

    def on_challenge(self):
        """
        Người dùng nhấn Challenge
        Lấy người được chọn trong listbox -> gửi lời thách
        """
        sel = self.users_listbox.curselection()
        if not sel:
            messagebox.showinfo('Info', 'Select a user to challenge')
            return
        
        opponent = self.users_listbox.get(sel[0])
        if opponent == self.name:
            messagebox.showinfo('Info', 'Cannot challenge yourself')
            return
        
        # Disable button để tránh spam
        self.challenge_btn['state'] = 'disabled'
        self.send_json({'type': 'challenge', 'opponent': opponent})
        self.append_chat(f'Challenge sent to {opponent}...\n', "system")

    def on_cell(self, x, y):
        """
        Người dùng click vào ô (x, y)
        Kiểm tra hợp lệ -> gửi move lên server
        """
        # Kiểm tra điều kiện
        if not self.in_match or self.you != self.turn:
            return
        if self.board_state[y][x]:  # Ô đã có quân
            return
        
        # Disable board (chờ server xác nhận)
        self.disable_board()
        # Gửi move
        self.send_json({'type': 'move', 'x': x, 'y': y})

    def on_send_chat(self, event=None):
        """
        Người dùng gửi chat (Enter hoặc click Send)
        """
        text = self.chat_entry.get().strip()
        if not text:
            return
        
        # Max 500 ký tự
        if len(text) > 500:
            messagebox.showwarning('Warning', 'Message too long (max 500 characters)')
            return
        
        # Gửi lên server (nếu đang trong trận)
        if self.in_match:
            self.send_json({'type': 'chat', 'text': text})
            self.append_chat(f'You: {text}\n', "you")
        else:
            self.append_chat(f'(Not in match) You: {text}\n', "system")
        
        # Clear input
        self.chat_entry.delete(0, tk.END)

    def start_async_loop(self):
        """
        Chạy trong thread riêng
        Tạo event loop mới -> connect server -> vòng lặp nhận message
        """
        try:
            # Tạo event loop mới cho thread này
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            # Chạy async_main (connect + receive loop)
            self.loop.run_until_complete(self.async_main())
        except Exception as e:
            print(f"[ERROR] Async loop error: {e}")
            if not self.is_closing:
                # Báo UI disconnect (qua queue)
                self.queue.put((self.handle_disconnect, ()))
        finally:
            # Cleanup: cancel tất cả task đang chạy
            if self.loop:
                pending = asyncio.all_tasks(self.loop)
                for task in pending:
                    task.cancel()
                
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                
                self.loop.close()
            
            # Reset state
            self.loop = None
            self.writer = None
            self.reader = None

    async def async_main(self):
        """
        Hàm chính của async thread
        1. Connect đến server
        2. Gửi login
        3. Vòng lặp nhận message
        """
        try:
            # Kết nối TCP
            self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
        except Exception as e:
            # Connect thất bại
            self.queue.put((self.set_status, (f'Connect failed: {e}',)))
            self.queue.put((self.handle_disconnect, ()))
            return

        # Gửi login
        await self.send_json_async({'type': 'login', 'name': self.name})

        try:
            # Vòng lặp nhận message
            while True:
                line = await self.reader.readline()  # Đọc 1 dòng
                if not line:  # Server đóng connection
                    break
                
                # Parse JSON
                msg = json.loads(line.decode('utf-8').strip())
                # Đẩy vào queue để main thread xử lý
                self.queue.put((self.handle_msg, (msg,)))
                
        except asyncio.CancelledError:
            print("[INFO] Connection cancelled")
        except Exception as e:
            if not self.is_closing:
                print(f"[ERROR] Connection error: {e}")
        finally:
            # Cleanup
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
        """
        Gửi JSON lên server (trong async context)
        Format: JSON + newline
        """
        if not self.writer or self.writer.is_closing():
            return
        try:
            data = json.dumps(obj, ensure_ascii=False) + '\n'
            self.writer.write(data.encode('utf-8'))
            await self.writer.drain()  # Đợi gửi xong
        except Exception as e:
            print(f"[ERROR] Send failed: {e}")

    def send_json(self, obj):
        """
        Gửi JSON từ main thread
        Dùng run_coroutine_threadsafe để gọi async function từ thread khác
        """
        if not self.writer or not self.loop:
            messagebox.showinfo('Info', 'Not connected')
            return
        if self.writer.is_closing():
            messagebox.showinfo('Info', 'Connection is closing')
            return
        
        # Schedule coroutine trong event loop
        asyncio.run_coroutine_threadsafe(self.send_json_async(obj), self.loop)

    # =====================================
    # UI HELPERS - Cập nhật giao diện
    # =====================================
    
    def process_queue(self):
        """
        Chạy định kỳ (100ms)
        Lấy message từ queue -> gọi handler tương ứng
        
        PATTERN:
        - Async thread: queue.put((function, args))
        - Main thread: lấy ra và gọi function(*args)
        """
        try:
            while True:
                fn, args = self.queue.get_nowait()  # Không block
                fn(*args)  # Gọi handler
        except Empty:
            pass
        # Schedule lại sau 100ms
        self.root.after(UPDATE_QUEUE_MS, self.process_queue)

    def set_status(self, text):
        """Cập nhật status label"""
        self.status_var.set(text)

    def append_chat(self, text, tag=None):
        """
        Thêm text vào chat area
        tag: "you" | "system" | None (để tô màu)
        """
        self.chat_area.config(state='normal')  # Enable edit
        if tag:
            self.chat_area.insert(tk.END, text, (tag,))
        else:
            self.chat_area.insert(tk.END, text)
        self.chat_area.config(state='disabled')  # Disable edit
        self.chat_area.see(tk.END)  # Scroll xuống cuối

    # =====================================
    # COUNTDOWN TIMER - Đồng hồ đếm ngược
    # =====================================
    
    def start_countdown(self, deadline):
        """
        Bắt đầu đếm ngược đến deadline
        deadline: timestamp (giây)
        """
        if not deadline:
            return
        self.deadline = deadline
        self.update_timer()

    def update_timer(self):
        """
        Cập nhật timer mỗi giây
        Còn > 5s: xanh lá
        Còn <= 5s: đỏ (cảnh báo)
        Hết giờ: gửi timeout lên server
        """
        if not self.deadline:
            self.timer_var.set('')
            return

        remaining = int(self.deadline - time.time())
        if remaining > 0:
            # Còn thời gian
            if remaining <= 5:
                self.timer_label.config(fg="#FF3B30")  # Đỏ
            else:
                self.timer_label.config(fg="#00FFAA")  # Xanh
            
            self.timer_var.set(f"{remaining}s left")
            # Schedule lại sau 1 giây
            self.timer_id = self.root.after(1000, self.update_timer)
        else:
            # HẾT GIỜ!
            self.timer_var.set("Time's up!")
            self.timer_label.config(fg="#FF3B30")
            self.stop_countdown()
            
            # Thông báo và gửi timeout lên server
            self.append_chat("Your time expired!\n", "system")
            self.set_status("You lost (timeout)")
            self.send_json({'type': 'timeout'})

    def stop_countdown(self):
        """
        Dừng timer (khi đã đi nước hoặc hết trận)
        """
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self.timer_var.set('')
        self.deadline = None
        self.timer_label.config(fg="#00FFAA")

    # =====================================
    # XỬ LÝ MESSAGE TỪ SERVER
    # =====================================
    
    def handle_disconnect(self):
        """
        Xử lý khi disconnect
        Reset tất cả state về ban đầu
        """
        self.set_status('Disconnected')
        self.connect_btn['state'] = 'normal'
        self.disconnect_btn['state'] = 'disabled'
        self.challenge_btn['state'] = 'disabled'
        self.users_listbox.delete(0, tk.END)
        self.clear_board()
        self.disable_board()
        self.stop_countdown()
        self.in_match = False
        self.highlighted = []
        self.is_closing = False

    def handle_msg(self, msg):
        """
        XỬ LÝ TẤT CẢ MESSAGE TỪ SERVER
        Đây là "bộ não" của client - routing message đến handler phù hợp
        """
        t = msg.get('type')
        
        # ========================================
        # LOGIN THÀNH CÔNG
        # ========================================
        if t == 'login_ok':
            self.set_status(f'Connected as {self.name}')
            self.connect_btn['state'] = 'disabled'
            self.disconnect_btn['state'] = 'normal'
            self.challenge_btn['state'] = 'normal'
            # Cập nhật danh sách người online
            self.update_users(msg.get('users', []))
            self.append_chat('=== Connected to server ===\n', "system")

        # ========================================
        # CẬP NHẬT DANH SÁCH NGƯỜI ONLINE
        # ========================================
        elif t == 'user_list':
            self.update_users(msg.get('users', []))

        # ========================================
        # ĐÃ GỬI LỜI THÁCH (feedback)
        # ========================================
        elif t == 'challenge_sent':
            to = msg.get('to')
            self.append_chat(f'Waiting for {to} to accept...\n', "system")
            self.challenge_btn['state'] = 'normal'

        # ========================================
        # NHẬN LỜI THÁCH TỪ AI ĐÓ
        # ========================================
        elif t == 'invite':
            frm = msg.get('from')
            # Hiện popup hỏi có chấp nhận không
            if messagebox.askyesno('Challenge', f'{frm} challenges you to a match!\n\nAccept?'):
                # Chấp nhận -> gửi accept
                self.send_json({'type': 'accept', 'opponent': frm})
            else:
                # Từ chối
                self.append_chat(f'Declined challenge from {frm}\n', "system")

        # ========================================
        # TRẬN ĐẤU BẮT ĐẦU
        # ========================================
        elif t == 'match_start':
            self.in_match = True
            self.you = msg.get('you')  # X hoặc O
            self.opponent = msg.get('opponent')
            self.turn = None
            self.clear_board()
            self.disable_board()
            self.set_status(f'Playing vs {self.opponent} (You: {self.you})')
            
            # Thông báo trong chat
            opp_symbol = "O" if self.you == "X" else "X"
            self.append_chat(f'\n=== Match Started: You ({self.you}) vs {self.opponent} ({opp_symbol}) ===\n', "system")
            
            # Resize board (phòng trường hợp board bị lỗi)
            self.root.after(100, self.on_canvas_resize)

        # ========================================
        # ĐẾN LƯỢT BẠN
        # ========================================
        elif t == 'your_turn':
            self.turn = self.you
            deadline = msg.get('deadline')
            if deadline:
                self.start_countdown(deadline)  # Bật timer
            self.enable_board()  # Cho phép click
            self.set_status("Your turn!")
            self.append_chat('Your turn!\n', "system")

        # ========================================
        # ĐỐI THỦ ĐI NƯỚC hoặc NƯỚC CỦA BẠN ĐÃ OK
        # ========================================
        elif t == 'opponent_move' or t == 'move_ok':
            x, y, sym = msg.get('x'), msg.get('y'), msg.get('symbol')
            self.set_cell(x, y, sym)  # Vẽ quân cờ
            self.turn = None
            self.stop_countdown()
            self.disable_board()
            
            if t == 'opponent_move':
                # Đối thủ vừa đi
                self.set_status(f"{self.opponent} played ({x}, {y})")
                self.append_chat(f'{self.opponent} played at ({x}, {y})\n', None)
            else:
                # Nước của bạn đã được server xác nhận
                self.append_chat(f'You played at ({x}, {y})\n', "you")

        # ========================================
        # HIGHLIGHT LINE THẮNG
        # ========================================
        elif t == 'highlight':
            cells = msg.get('cells', [])  # [(x1,y1), (x2,y2), ...]
            winner_name = msg.get('winner', '')
            self.highlight_winning_line(cells)
            
            if winner_name == self.name:
                self.set_status("You win!")
            else:
                self.set_status(f"{winner_name} wins!")

        # ========================================
        # TRẬN ĐẤU KẾT THÚC
        # ========================================
        elif t == 'match_end':
            result = msg.get('result')  # 'win' | 'lose' | 'draw'
            reason = msg.get('reason', '')  # 'win' | 'timeout' | 'disconnect' | 'draw'
            
            # Hiển thị kết quả với emoji dễ thương
            if result == 'win':
                msg_text = f"🎉 You won! ({reason})"
                self.append_chat(f'\n{msg_text}\n', "system")
                messagebox.showinfo("Victory!", msg_text)
            elif result == 'lose':
                msg_text = f"😢 You lost ({reason})"
                self.append_chat(f'\n{msg_text}\n', "system")
                messagebox.showinfo("Defeat", msg_text)
            elif result == 'draw':
                msg_text = "🤝 Draw!"
                self.append_chat(f'\n{msg_text}\n', "system")
                messagebox.showinfo("Draw", msg_text)
            
            # Reset state
            self.clear_board()
            self.disable_board()
            self.stop_countdown()
            self.in_match = False
            self.highlighted = []
            self.set_status('Match ended')

        # ========================================
        # NHẬN TIN NHẮN CHAT
        # ========================================
        elif t == 'chat':
            sender = msg.get('from')
            text = msg.get('text')
            self.append_chat(f'{sender}: {text}\n', None)

        # ========================================
        # LỖI TỪ SERVER
        # ========================================
        elif t == 'error':
            error_msg = msg.get('msg', 'Unknown error')
            self.append_chat(f'Error: {error_msg}\n', "system")
            
            # Chỉ hiện popup cho lỗi quan trọng (login, name...)
            if 'name' in error_msg.lower() or 'login' in error_msg.lower():
                messagebox.showerror('Error', error_msg)
            
            # Re-enable challenge button nếu không trong trận
            if not self.in_match:
                self.challenge_btn['state'] = 'normal'

    def update_users(self, users):
        """
        Cập nhật danh sách người online
        Giữ nguyên selection nếu có thể (UX tốt hơn)
        """
        # Lưu lại người đang được chọn
        current_selection = None
        if self.users_listbox.curselection():
            current_selection = self.users_listbox.get(self.users_listbox.curselection()[0])
        
        # Xóa list cũ
        self.users_listbox.delete(0, tk.END)
        new_index = None
        
        # Thêm lại từng user
        for i, u in enumerate(users):
            self.users_listbox.insert(tk.END, u)
            # Tìm index của user đã chọn trước đó
            if u == current_selection:
                new_index = i
        
        # Restore selection
        if new_index is not None:
            self.users_listbox.selection_set(new_index)


def main():
    """
    ENTRY POINT - Điểm khởi đầu của chương trình
    """
    root = tk.Tk()
    app = GuiClient(root)
    
    def on_closing():
        """
        Xử lý khi người dùng đóng cửa sổ (click X)
        Hỏi xác nhận -> disconnect -> đóng app
        """
        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            app.on_disconnect()  # Disconnect khỏi server
            root.after(500, root.destroy)  # Đợi 0.5s rồi đóng
    
    # Bind sự kiện đóng cửa sổ
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Khởi động UI loop (blocking call)
    root.mainloop()


if __name__ == '__main__':
    main()
