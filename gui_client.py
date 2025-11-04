import asyncio
import json
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog
from queue import Queue, Empty

HOST = '127.0.0.1'
PORT = 7777
BOARD_SIZE = 15


class GuiClient:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('Caro - GUI Client')
        self.root.minsize(600, 450)
        
        self.queue: Queue = Queue()
        self.reader = None
        self.writer = None
        self.loop = None
        self.name = ''
        self.in_match = False
        self.you = None
        self.opponent = None
        self.turn = None

        top = tk.Frame(root)
        top.pack(side='top', fill='x')
        tk.Label(top, text='Name:').pack(side='left')
        self.name_var = tk.StringVar(value='Player')
        tk.Entry(top, textvariable=self.name_var, width=12).pack(side='left')
        self.connect_btn = tk.Button(top, text='Connect', command=self.on_connect)
        self.connect_btn.pack(side='left')
        self.disconnect_btn = tk.Button(top, text='Disconnect', command=self.on_disconnect, state='disabled')
        self.disconnect_btn.pack(side='left')

        self.users_listbox = tk.Listbox(root, height=6)
        self.users_listbox.pack(side='left', fill='y', padx=4, pady=4)
        
        btn_frame = tk.Frame(root)
        btn_frame.pack(side='left', fill='y')
        self.challenge_btn = tk.Button(btn_frame, text='Challenge', command=self.on_challenge, state='disabled')
        self.challenge_btn.pack(pady=2)

        board_frame = tk.Frame(root)
        board_frame.pack(side='left', fill='both', expand=True, padx=8, pady=8)
        
        self.cells = []
        for y in range(BOARD_SIZE):
            row = []
            for x in range(BOARD_SIZE):
                b = tk.Button(board_frame, text='', width=2, height=1, command=lambda xx=x, yy=y: self.on_cell(xx, yy))
                b.grid(row=y, column=x, sticky='nsew')
                b['state'] = 'disabled' # Vô hiệu hóa bàn cờ ban đầu
                row.append(b)
            self.cells.append(row)

        for i in range(BOARD_SIZE):
            board_frame.grid_rowconfigure(i, weight=1)
            board_frame.grid_columnconfigure(i, weight=1)

        status = tk.Frame(root)
        status.pack(side='bottom', fill='x')
        self.status_var = tk.StringVar(value='Not connected')
        tk.Label(status, textvariable=self.status_var).pack(side='left')

        self.root.after(100, self.process_queue)

    def on_connect(self):
        if self.writer:
            messagebox.showinfo('Info', 'Already connected')
            return
        self.name = self.name_var.get().strip() or 'Player'
        self.status_var.set('Connecting...')
        self.connect_btn['state'] = 'disabled'
        threading.Thread(target=self.start_async_loop, daemon=True).start()

    def on_disconnect(self):
        self.status_var.set('Disconnecting...')
        if self.writer and self.loop:
            # Gửi tác vụ đóng writer lên event loop
            self.loop.call_soon_threadsafe(self.writer.close)
        # Các việc dọn dẹp khác (như self.loop = None) sẽ 
        # được thực hiện trong finally của async_main

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
        if not self.in_match:
            return
        if self.you != self.turn:
            messagebox.showinfo('Info', "Not your turn")
            return
        
        # Vô hiệu hóa bàn cờ ngay sau khi bấm
        self.disable_board()
        self.send_json({'type': 'move', 'x': x, 'y': y})

    def process_queue(self):
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
                    print("Server closed connection (empty line)")
                    break
                try:
                    msg = json.loads(line.decode('utf-8').strip())
                    # Gửi tin nhắn về luồng UI để xử lý
                    self.queue.put((self.handle_msg, (msg,)))
                except json.JSONDecodeError:
                    print(f"Received invalid JSON: {line.decode('utf-8')}")
                except Exception as e:
                    print(f"Error processing message: {e}")
        
        except ConnectionError as e:
            print(f'Connection lost: {e}')
        except asyncio.CancelledError:
            print("Read loop cancelled.")
        except Exception as e:
            print(f'Read loop error: {e}')
        finally:
            # Bất kể lý do gì, khi vòng lặp đọc kết thúc -> xử lý ngắt kết nối
            self.queue.put((self.handle_disconnect, ()))
            if self.writer:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except: pass
            self.writer = None
            self.reader = None


    async def send_json_async(self, obj):
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
        if not self.writer or not self.loop or self.writer.is_closing():
            messagebox.showinfo('Info', 'Not connected')
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
    # UI HANDLERS (Chạy trên luồng chính)
    # ========================

    def set_status(self, s):
        self.status_var.set(s)
    
    def enable_board(self):
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                 self.cells[y][x]['state'] = 'normal'

    def disable_board(self):
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                 self.cells[y][x]['state'] = 'disabled'

    def handle_disconnect(self):
        """Hàm dọn dẹp giao diện khi ngắt kết nối."""
        self.set_status('Disconnected')
        self.connect_btn['state'] = 'normal'
        self.disconnect_btn['state'] = 'disabled'
        self.challenge_btn['state'] = 'disabled'
        self.users_listbox.delete(0, tk.END)
        self.clear_board()
        self.disable_board()
        
        if self.in_match:
            messagebox.showinfo("Info", "Trận đấu đã kết thúc do mất kết nối.")
        
        self.in_match = False
        self.you = None
        self.opponent = None
        self.turn = None

    def handle_msg(self, msg):
        t = msg.get('type')
        
        if t == 'login_ok':
            self.set_status(f'Connected as {self.name}')
            self.connect_btn['state'] = 'disabled'
            self.disconnect_btn['state'] = 'normal'
            self.challenge_btn['state'] = 'normal'
            users = msg.get('users', [])
            self.update_users(users)
        
        elif t == 'user_list':
            self.update_users(msg.get('users', []))
        
        elif t == 'invite':
            frm = msg.get('from')
            if self.in_match: # Từ chối tự động nếu đang bận
                self.send_json({'type': 'reject', 'opponent': frm})
                return
            
            if messagebox.askyesno('Invite', f'Accept challenge from {frm}?'):
                self.send_json({'type': 'accept', 'opponent': frm})
            # else: # Gửi từ chối (nếu server hỗ trợ)
            #     self.send_json({'type': 'reject', 'opponent': frm})

        elif t == 'match_start':
            self.in_match = True
            self.you = msg.get('you')
            self.opponent = msg.get('opponent')
            self.turn = None # Chờ 'your_turn'
            self.clear_board()
            self.disable_board() # Vô hiệu hóa cho đến khi 'your_turn'
            self.set_status(f'In match vs {self.opponent}. You are "{self.you}"')
        
        elif t == 'your_turn':
            self.turn = self.you
            self.set_status('Your turn!')
            self.enable_board() # Kích hoạt bàn cờ
        
        elif t == 'opponent_move':
            x = msg.get('x'); y = msg.get('y'); sym = msg.get('symbol')
            self.set_cell(x, y, sym)
        
        elif t == 'move_ok':
            x = msg.get('x'); y = msg.get('y'); sym = msg.get('symbol')
            self.set_cell(x, y, sym)
            self.turn = None # Chờ lượt đối thủ
            self.set_status('Waiting for opponent...')
            self.disable_board() # Vô hiệu hóa
        
        elif t == 'highlight':
            cells = msg.get('cells', [])
            winner = msg.get('winner', '')
            for (x, y) in cells:
                if 0 <= y < BOARD_SIZE and 0 <= x < BOARD_SIZE:
                    self.cells[y][x]['bg'] = 'yellow'
            self.set_status(f'{winner} wins! Highlighting...')
    
        elif t == 'match_end':
            result = msg.get('result') 
            reason = msg.get('reason', 'ended') 
            
            message_text = "Trận đấu kết thúc."

            if result == 'win':
                if reason == 'timeout':
                    message_text = 'Bạn đã thắng (Đối thủ hết giờ)!'
                elif reason == 'disconnect':
                    message_text = 'Bạn đã thắng (Đối thủ ngắt kết nối)!'
                else: # reason == 'win'
                    message_text = 'Bạn đã thắng!'
            
            elif result == 'lose':
                if reason == 'timeout':
                    message_text = 'Bạn đã thua (Bạn hết giờ)!'
                elif reason == 'disconnect':
                    message_text = 'Bạn đã thua (Bạn ngắt kết nối)!'
                else: # reason == 'win'
                    message_text = 'Bạn đã thua!'
            
            elif reason == 'tie': # Dành cho tương lai nếu có xử lý hòa
                message_text = 'Trận đấu hòa!'
            
            messagebox.showinfo('Kết thúc', message_text)

            self.in_match = False
            self.you = None
            self.opponent = None
            self.turn = None
            self.set_status('Idle. Select a user to challenge.')
            self.clear_board()
            self.disable_board() # Vô hiệu hóa bàn cờ sau khi trận kết thúc

        elif t == 'error':
            errmsg = msg.get('msg', '')
            messagebox.showerror('Error', errmsg)
            if errmsg == "Name already in use":
                self.handle_disconnect() # Xử lý như ngắt kết nối

    def update_users(self, users):
        self.users_listbox.delete(0, tk.END)
        for u in users:
            self.users_listbox.insert(tk.END, u)

    def clear_board(self):
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                self.cells[y][x]['text'] = ''
                self.cells[y][x]['bg'] = 'SystemButtonFace' # Màu nút mặc định

    def set_cell(self, x, y, symbol):
        if 0 <= y < BOARD_SIZE and 0 <= x < BOARD_SIZE:
             self.cells[y][x]['text'] = symbol
        else:
             print(f"Invalid cell coordinates: ({x}, {y})")


def main():
    root = tk.Tk()
    app = GuiClient(root)
    
    def on_closing():
        print("Closing application...")
        app.on_disconnect()
        
        try:
            if app.loop and app.loop.is_running():
                # Dừng loop từ luồng chính
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