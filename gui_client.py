import asyncio
import json
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, scrolledtext
from queue import Queue, Empty
import time

HOST = '127.0.0.1'
PORT = 7777
BOARD_SIZE = 15


class GuiClient:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('Caro Game - Multiplayer')
        self.root.geometry('1000x700')
        
        self.queue: Queue = Queue()
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
        
        # ===== TOP PANEL: Kết nối =====
        top = tk.Frame(root, relief='groove', bd=2)
        top.pack(side='top', fill='x', padx=5, pady=5)
        
        tk.Label(top, text='Tên:', font=('Arial', 10)).pack(side='left', padx=5)
        self.name_var = tk.StringVar(value='Player')
        tk.Entry(top, textvariable=self.name_var, width=15, font=('Arial', 10)).pack(side='left', padx=5)
        
        self.connect_btn = tk.Button(top, text='Kết nối', command=self.on_connect, 
                                     bg='#4CAF50', fg='white', font=('Arial', 10, 'bold'), 
                                     padx=10, pady=5)
        self.connect_btn.pack(side='left', padx=5)
        
        self.disconnect_btn = tk.Button(top, text='Ngắt kết nối', command=self.on_disconnect, 
                                       state='disabled', bg='#f44336', fg='white', 
                                       font=('Arial', 10, 'bold'), padx=10, pady=5)
        self.disconnect_btn.pack(side='left', padx=5)
        
        # Timer label
        self.timer_var = tk.StringVar(value='')
        tk.Label(top, textvariable=self.timer_var, font=('Arial', 12, 'bold'), fg='red').pack(side='right', padx=10)

        # ===== MAIN CONTAINER =====
        main_container = tk.Frame(root)
        main_container.pack(side='top', fill='both', expand=True, padx=5, pady=5)
        
        # Tắt auto-propagation để tránh lag khi resize
        main_container.pack_propagate(True)

        # ===== LEFT PANEL: Danh sách người chơi =====
        left_panel = tk.Frame(main_container, relief='groove', bd=2, width=200)
        left_panel.pack(side='left', fill='y', padx=(0, 5))
        left_panel.pack_propagate(False)
        
        tk.Label(left_panel, text='Người chơi online', font=('Arial', 11, 'bold')).pack(pady=5)
        
        self.users_listbox = tk.Listbox(left_panel, font=('Arial', 10))
        self.users_listbox.pack(padx=5, pady=5, fill='both', expand=True)
        
        self.challenge_btn = tk.Button(left_panel, text='Thách đấu', command=self.on_challenge, 
                                      state='disabled', bg='#2196F3', fg='white', 
                                      font=('Arial', 10, 'bold'), pady=5)
        self.challenge_btn.pack(pady=5, fill='x', padx=5)

        # ===== CENTER PANEL: Bàn cờ =====
        center_panel = tk.Frame(main_container)
        center_panel.pack(side='left', fill='both', expand=True, padx=5)
        
        # Match info
        match_info = tk.Frame(center_panel, relief='groove', bd=2)
        match_info.pack(fill='x', pady=(0, 5))
        
        self.match_info_var = tk.StringVar(value='Chưa có trận đấu')
        tk.Label(match_info, textvariable=self.match_info_var, 
                font=('Arial', 11, 'bold'), fg='blue', pady=5).pack()
        
        # Board container với kích thước cố định
        board_container = tk.Frame(center_panel, relief='sunken', bd=2, bg='#8B4513')
        board_container.pack(expand=True, pady=5)
        
        # Board frame - chứa bàn cờ 15x15
        self.board_frame = tk.Frame(board_container, bg='#8B4513', padx=2, pady=2)
        self.board_frame.pack()
        
        self.cells = []
        for y in range(BOARD_SIZE):
            row = []
            for x in range(BOARD_SIZE):
                b = tk.Button(self.board_frame, text='', width=4, height=2,
                            font=('Arial', 10, 'bold'),
                            command=lambda xx=x, yy=y: self.on_cell(xx, yy),
                            bg='#F5DEB3', activebackground='#FFE4B5',
                            relief='solid', bd=1, cursor='hand2')
                b.grid(row=y, column=x, padx=0, pady=0, sticky='nsew')
                b['state'] = 'disabled'
                row.append(b)
                # Cấu hình để ô có kích thước đồng đều
                self.board_frame.grid_columnconfigure(x, weight=1)
                self.board_frame.grid_rowconfigure(y, weight=1)
            self.cells.append(row)

        # ===== RIGHT PANEL: Chat =====
        right_panel = tk.Frame(main_container, relief='groove', bd=2, width=280)
        right_panel.pack(side='right', fill='y', padx=(5, 0))
        right_panel.pack_propagate(False)
        
        tk.Label(right_panel, text='Chat', font=('Arial', 11, 'bold')).pack(pady=5)
        
        self.chat_text = scrolledtext.ScrolledText(right_panel, font=('Arial', 9), 
                                                   state='disabled', wrap='word')
        self.chat_text.pack(padx=5, pady=5, fill='both', expand=True)
        
        chat_input_frame = tk.Frame(right_panel)
        chat_input_frame.pack(fill='x', padx=5, pady=5)
        
        self.chat_entry = tk.Entry(chat_input_frame, font=('Arial', 10))
        self.chat_entry.pack(side='left', fill='x', expand=True, padx=(0, 5))
        self.chat_entry.bind('<Return>', lambda e: self.on_send_chat())
        
        self.send_chat_btn = tk.Button(chat_input_frame, text='Gửi', command=self.on_send_chat,
                                      bg='#4CAF50', fg='white', font=('Arial', 9, 'bold'))
        self.send_chat_btn.pack(side='right')

        # ===== BOTTOM: Status bar =====
        status = tk.Frame(root, relief='sunken', bd=1)
        status.pack(side='bottom', fill='x')
        
        self.status_var = tk.StringVar(value='Chưa kết nối')
        tk.Label(status, textvariable=self.status_var, anchor='w', font=('Arial', 9)).pack(
            side='left', fill='x', expand=True, padx=5, pady=2)

        
        self.root.after(100, self.process_queue)


    def on_connect(self):
        """Xử lý khi nhấn nút Kết nối"""
        if self.writer:
            messagebox.showinfo('Thông báo', 'Đã kết nối rồi')
            return
        self.name = self.name_var.get().strip() or 'Player'
        self.status_var.set('Đang kết nối...')
        self.connect_btn['state'] = 'disabled'
        threading.Thread(target=self.start_async_loop, daemon=True).start()

    def on_disconnect(self):
        """Xử lý khi nhấn nút Ngắt kết nối"""
        self.status_var.set('Đang ngắt kết nối...')
        if self.writer and self.loop:
            self.loop.call_soon_threadsafe(self.writer.close)

    def on_challenge(self):
        """Xử lý khi nhấn nút Thách đấu"""
        sel = self.users_listbox.curselection()
        if not sel:
            messagebox.showinfo('Thông báo', 'Chọn một người chơi để thách đấu')
            return
        opponent_display = self.users_listbox.get(sel[0])
        # Loại bỏ phần " (Bạn)" và ký tự bullet
        opponent = opponent_display.replace(' (Bạn)', '').replace('• ', '').strip()
        
        if opponent == self.name:
            messagebox.showinfo('Thông báo', 'Không thể thách đấu chính mình')
            return
        
        if self.in_match:
            messagebox.showinfo('Thông báo', 'Bạn đang trong trận đấu')
            return
            
        self.send_json({'type': 'challenge', 'opponent': opponent})
        self.add_chat_msg(f"Hệ thống: Đã gửi lời thách đấu đến {opponent}")
        self.set_status(f'Đã gửi lời thách đấu đến {opponent}...')

    def on_cell(self, x, y):
        """Xử lý khi click vào ô trên bàn cờ"""
        if not self.in_match:
            return
        if self.you != self.turn:
            messagebox.showinfo('Thông báo', "Chưa đến lượt bạn")
            return
        
        self.disable_board()
        self.send_json({'type': 'move', 'x': x, 'y': y})
        self.stop_timer()

    def on_send_chat(self):
        """Xử lý gửi tin nhắn chat"""
        text = self.chat_entry.get().strip()
        if not text:
            return
        if not self.in_match:
            messagebox.showinfo('Thông báo', 'Chỉ có thể chat khi đang trong trận đấu')
            return
        
        self.send_json({'type': 'chat', 'text': text})
        self.add_chat_msg(f"Bạn: {text}")
        self.chat_entry.delete(0, tk.END)

    def process_queue(self):
        """Xử lý hàng đợi message từ async thread"""
        try:
            while True:
                fn, args = self.queue.get_nowait()
                try:
                    fn(*args)
                except Exception as e:
                    print(f'UI handler error: {e}')
        except Empty:
            pass
        self.root.after(100, self.process_queue)

    def start_async_loop(self):
        """Khởi động async event loop trong thread riêng"""
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
        try:
            self.loop.run_until_complete(self.async_main())
        except Exception as e:
            print(f"Async loop error: {e}")
            self.queue.put((self.handle_disconnect, ()))
        finally:
            self.loop.close()
            self.loop = None
            print("Async loop closed.")

    async def async_main(self):
        """Vòng lặp chính xử lý kết nối và nhận message"""
        try:
            self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
        except Exception as e:
            self.queue.put((self.set_status, (f'Kết nối thất bại: {e}',)))
            self.queue.put((self.handle_disconnect, ()))
            return
        
        await self.send_json_async({'type': 'login', 'name': self.name})
        
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    print("Server closed connection")
                    break
                try:
                    msg = json.loads(line.decode('utf-8').strip())
                    self.queue.put((self.handle_msg, (msg,)))
                except json.JSONDecodeError:
                    print(f"Invalid JSON: {line.decode('utf-8')}")
                except Exception as e:
                    print(f"Error processing message: {e}")
        
        except ConnectionError as e:
            print(f'Connection lost: {e}')
        except asyncio.CancelledError:
            print("Read loop cancelled.")
        except Exception as e:
            print(f'Read loop error: {e}')
        finally:
            self.queue.put((self.handle_disconnect, ()))
            if self.writer:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except: pass
            self.writer = None
            self.reader = None

    async def send_json_async(self, obj):
        """Gửi JSON qua socket (async)"""
        if not self.writer or self.writer.is_closing():
            return
        data = json.dumps(obj, ensure_ascii=False) + '\n'
        self.writer.write(data.encode('utf-8'))
        try:
            await self.writer.drain()
        except Exception as e:
            print(f"Error draining writer: {e}")
            if self.writer:
                self.writer.close()
            self.writer = None

    def send_json(self, obj):
        """Gửi JSON từ UI thread"""
        if not self.writer or not self.loop or self.writer.is_closing():
            messagebox.showinfo('Thông báo', 'Chưa kết nối')
            return
        
        try:
            if self.loop.is_running():
                asyncio.run_coroutine_threadsafe(self.send_json_async(obj), self.loop)
            else:
                print("Event loop is not running. Cannot send.")
        except RuntimeError as e:
             print(f"Error sending json: {e}")
             self.queue.put((self.handle_disconnect, ()))

    # ========================
    # UI HANDLERS
    # ========================

    def set_status(self, s):
        """Cập nhật status bar"""
        self.status_var.set(s)
    
    def enable_board(self):
        """Kích hoạt bàn cờ - cho phép click"""
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                if self.cells[y][x]['text'] == '':
                    self.cells[y][x]['state'] = 'normal'

    def disable_board(self):
        """Vô hiệu hóa bàn cờ"""
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                self.cells[y][x]['state'] = 'disabled'

    def add_chat_msg(self, msg):
        """Thêm tin nhắn vào khung chat"""
        self.chat_text['state'] = 'normal'
        self.chat_text.insert(tk.END, msg + '\n')
        self.chat_text.see(tk.END)
        self.chat_text['state'] = 'disabled'

    def start_timer(self, deadline):
        """Bắt đầu đếm ngược thời gian"""
        self.deadline = deadline
        self.update_timer()
    
    def stop_timer(self):
        """Dừng timer"""
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self.timer_var.set('')
        self.deadline = None
    
    def update_timer(self):
        """Cập nhật hiển thị timer"""
        if self.deadline is None:
            return
        
        remaining = int(self.deadline - time.time())
        if remaining < 0:
            remaining = 0
        
        self.timer_var.set(f'⏱ {remaining}s')
        
        if remaining > 0:
            self.timer_id = self.root.after(1000, self.update_timer)
        else:
            self.timer_var.set('⏱ HẾT GIỜ!')

    def handle_disconnect(self):
        """Xử lý khi ngắt kết nối"""
        self.set_status('Đã ngắt kết nối')
        self.connect_btn['state'] = 'normal'
        self.disconnect_btn['state'] = 'disabled'
        self.challenge_btn['state'] = 'disabled'
        self.users_listbox.delete(0, tk.END)
        self.clear_board()
        self.disable_board()
        self.stop_timer()
        
        if self.in_match:
            messagebox.showinfo("Thông báo", "Trận đấu đã kết thúc do mất kết nối.")
        
        self.in_match = False
        self.you = None
        self.opponent = None
        self.turn = None
        self.match_info_var.set('Chưa có trận đấu')

    def handle_msg(self, msg):
        """Xử lý các message từ server"""
        t = msg.get('type')
        
        if t == 'login_ok':
            self.set_status(f'Đã kết nối với tên: {self.name}')
            self.connect_btn['state'] = 'disabled'
            self.disconnect_btn['state'] = 'normal'
            self.challenge_btn['state'] = 'normal'
            users = msg.get('users', [])
            self.update_users(users)
            self.add_chat_msg('Hệ thống: Đã kết nối thành công!')
        
        elif t == 'user_list':
            self.update_users(msg.get('users', []))
        
        elif t == 'invite':
            frm = msg.get('from')
            if self.in_match:
                return
            
            self.add_chat_msg(f'Hệ thống: {frm} muốn thách đấu với bạn!')
            if messagebox.askyesno('Lời mời', f'Chấp nhận thách đấu từ {frm}?'):
                self.send_json({'type': 'accept', 'opponent': frm})

        elif t == 'match_start':
            self.in_match = True
            self.you = msg.get('you')
            self.opponent = msg.get('opponent')
            self.turn = None
            self.clear_board()
            self.disable_board()
            
            # Reset chat khi bắt đầu ván mới
            self.chat_text['state'] = 'normal'
            self.chat_text.delete(1.0, tk.END)
            self.chat_text['state'] = 'disabled'
            
            symbol_display = '❌ (X)' if self.you == 'X' else '⭕ (O)'
            self.match_info_var.set(f'Bạn: {symbol_display} | Đối thủ: {self.opponent}')
            self.set_status(f'Trận đấu với {self.opponent} đã bắt đầu!')
            self.add_chat_msg(f'Hệ thống: Trận đấu bắt đầu! Bạn là {self.you}')
        
        elif t == 'your_turn':
            self.turn = self.you
            deadline = msg.get('deadline')
            
            if deadline:
                self.start_timer(deadline)
                self.set_status('Đến lượt bạn!')
            else:
                self.stop_timer()
                self.set_status('Đến lượt bạn! (Chưa bắt đầu đếm giờ)')
            
            self.enable_board()
            self.add_chat_msg('Hệ thống: Đến lượt bạn!')
        
        elif t == 'opponent_move':
            x = msg.get('x')
            y = msg.get('y')
            sym = msg.get('symbol')
            self.set_cell(x, y, sym)
            self.add_chat_msg(f'Hệ thống: Đối thủ đánh tại ({x}, {y})')
        
        elif t == 'move_ok':
            x = msg.get('x')
            y = msg.get('y')
            sym = msg.get('symbol')
            self.set_cell(x, y, sym)
            self.turn = None
            self.set_status('Đang đợi đối thủ...')
            self.disable_board()
            self.stop_timer()
            self.add_chat_msg(f'Hệ thống: Bạn đánh tại ({x}, {y})')
        
        elif t == 'highlight':
            cells = msg.get('cells', [])
            winner = msg.get('winner', '')
            # Reset màu của tất cả các ô về mặc định trước
            for y in range(BOARD_SIZE):
                for x in range(BOARD_SIZE):
                    self.cells[y][x]['bg'] = '#F5DEB3'
                    if self.cells[y][x]['text'] == '✖':
                        self.cells[y][x]['fg'] = '#E53935'
                    elif self.cells[y][x]['text'] == '⭕':
                        self.cells[y][x]['fg'] = '#1E88E5'
            # Chỉ highlight những ô trong dãy thắng
            for (x, y) in cells:
                if 0 <= y < BOARD_SIZE and 0 <= x < BOARD_SIZE:
                    self.cells[y][x]['bg'] = '#FFD700'  # Màu vàng cho dãy thắng
            self.set_status(f'{winner} thắng! Đang hiển thị dãy thắng...')
            self.disable_board()
            self.stop_timer()
    
        elif t == 'match_end':
            result = msg.get('result') 
            reason = msg.get('reason', 'ended')
            
            message_text = "Trận đấu kết thúc."

            if result == 'win':
                if reason == 'timeout':
                    message_text = '🎉 Bạn đã thắng!\n(Đối thủ hết giờ)'
                elif reason == 'disconnect':
                    message_text = '🎉 Bạn đã thắng!\n(Đối thủ đã thoát game)'
                    self.add_chat_msg('Hệ thống: ⚠️ Đối thủ đã thoát game. Bạn thắng!')
                else:
                    message_text = '🎉 Chúc mừng! Bạn đã thắng!'
            
            elif result == 'lose':
                if reason == 'timeout':
                    message_text = '😢 Bạn đã thua!\n(Bạn hết giờ)'
                elif reason == 'disconnect':
                    message_text = '😢 Bạn đã thua!\n(Bạn đã thoát game)'
                    self.add_chat_msg('Hệ thống: ⚠️ Bạn đã thoát game. Bạn thua!')
                else:
                    message_text = '😢 Rất tiếc! Bạn đã thua!'
            
            elif reason == 'tie':
                message_text = '🤝 Trận đấu hòa!'
            
            messagebox.showinfo('Kết thúc', message_text)
            self.add_chat_msg(f'Hệ thống: {message_text}')

            self.in_match = False
            self.you = None
            self.opponent = None
            self.turn = None
            self.set_status('Rảnh rỗi. Chọn người chơi để thách đấu.')
            self.match_info_var.set('Chưa có trận đấu')
            self.clear_board()
            self.disable_board()
            self.stop_timer()
        
        elif t == 'chat':
            frm = msg.get('from', '')
            text = msg.get('text', '')
            self.add_chat_msg(f'{frm}: {text}')

        elif t == 'error':
            errmsg = msg.get('msg', '')
            messagebox.showerror('Lỗi', errmsg)
            self.add_chat_msg(f'Lỗi: {errmsg}')
            if errmsg == "Name already in use":
                self.handle_disconnect()

    def update_users(self, users):
        """Cập nhật danh sách người chơi online"""
        self.users_listbox.delete(0, tk.END)
        for u in users:
            display = f'• {u}'
            if u == self.name:
                display += ' (Bạn)'
            self.users_listbox.insert(tk.END, display)

    def clear_board(self):
        """Xóa sạch bàn cờ"""
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                self.cells[y][x]['text'] = ''
                self.cells[y][x]['bg'] = '#F5DEB3'
                self.cells[y][x]['fg'] = 'black'

    def set_cell(self, x, y, symbol):
        """Đặt ký hiệu vào ô (x, y)"""
        if 0 <= y < BOARD_SIZE and 0 <= x < BOARD_SIZE:
            # Hiển thị X và O với màu sắc và hiệu ứng đẹp hơn
            if symbol == 'X':
                self.cells[y][x]['text'] = 'X'
                self.cells[y][x]['fg'] = '#FF0000'  # Đỏ tươi
                self.cells[y][x]['bg'] = '#FFE4E1'  # Nền hồng nhạt
            else:  # O
                self.cells[y][x]['text'] = 'O'
                self.cells[y][x]['fg'] = '#0000FF'  # Xanh dương
                self.cells[y][x]['bg'] = '#E0FFFF'  # Nền xanh nhạt
            
            # Không thay đổi relief và border để giữ nguyên kích thước
            # Không thay đổi font size để tránh ô bị giãn
        else:
            print(f"Invalid cell coordinates: ({x}, {y})")


def main():
    root = tk.Tk()
    app = GuiClient(root)
    
    def on_closing():
        """Xử lý khi đóng cửa sổ"""
        print("Closing application...")
        app.on_disconnect()
        
        try:
            if app.loop and app.loop.is_running():
                app.loop.call_soon_threadsafe(app.loop.stop)
                print("Requested asyncio loop stop.")
        except RuntimeError:
            pass
        except Exception as e:
            print(f"Error stopping loop: {e}")
            
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == '__main__':
    main()
