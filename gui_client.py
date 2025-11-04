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
        self.queue: Queue = Queue()
        self.reader = None
        self.writer = None
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
        tk.Button(top, text='Connect', command=self.on_connect).pack(side='left')
        tk.Button(top, text='Disconnect', command=self.on_disconnect).pack(side='left')

        self.users_listbox = tk.Listbox(root, height=6)
        self.users_listbox.pack(side='left', fill='y', padx=4, pady=4)
        btn_frame = tk.Frame(root)
        btn_frame.pack(side='left', fill='y')
        tk.Button(btn_frame, text='Challenge', command=self.on_challenge).pack(pady=2)

        board_frame = tk.Frame(root)
        board_frame.pack(side='left', padx=8, pady=8)
        self.cells = []
        for y in range(BOARD_SIZE):
            row = []
            for x in range(BOARD_SIZE):
                b = tk.Button(board_frame, text='', width=2, command=lambda xx=x, yy=y: self.on_cell(xx, yy))
                b.grid(row=y, column=x)
                row.append(b)
            self.cells.append(row)

        status = tk.Frame(root)
        status.pack(side='bottom', fill='x')
        self.status_var = tk.StringVar(value='Not connected')
        tk.Label(status, textvariable=self.status_var).pack(side='left')

        # poll queue
        self.root.after(100, self.process_queue)

    def on_connect(self):
        if self.writer:
            messagebox.showinfo('Info', 'Already connected')
            return
        self.name = self.name_var.get().strip() or 'Player'
        self.status_var.set('Connecting...')
        threading.Thread(target=self.start_async_loop, daemon=True).start()

    def on_disconnect(self):
        if self.writer:
            try:
                self.writer.close()
            except: pass
        self.writer = None
        self.reader = None
        self.status_var.set('Disconnected')

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
        # send move
        self.send_json({'type': 'move', 'x': x, 'y': y})

    def process_queue(self):
        try:
            while True:
                fn, args = self.queue.get_nowait()
                try:
                    fn(*args)
                except Exception as e:
                    print('UI handler error', e)
        except Empty:
            pass
        self.root.after(100, self.process_queue)

    def start_async_loop(self):
        try:
            asyncio.run(self.async_main())
        except RuntimeError as e:
            # Xử lý trường hợp event loop đã chạy 
            if "cannot run_until_complete" in str(e):
                print("Async loop already running. Scheduling async_main.")
                asyncio.run_coroutine_threadsafe(self.async_main(), asyncio.get_event_loop())
            else:
                raise

    async def async_main(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
        except Exception as e:
            self.queue.put((self.set_status, (f'Connect failed: {e}',)))
            return
        # send login
        await self.send_json_async({'type': 'login', 'name': self.name})
        self.queue.put((self.set_status, ('Connected',)))
        # read loop
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode('utf-8').strip())
                except Exception:
                    continue
                # dispatch to UI
                self.queue.put((self.handle_msg, (msg,)))
        except Exception as e:
            print('read loop error', e)
        finally:
            self.queue.put((self.set_status, ('Disconnected',)))
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None
            self.reader = None


    async def send_json_async(self, obj):
        if not self.writer:
            return
        data = json.dumps(obj, ensure_ascii=False) + '\n'
        self.writer.write(data.encode('utf-8'))
        try:
            await self.writer.drain()
        except Exception as e:
            print(f"Error draining writer: {e}")
            # Có thể đóng kết nối ở đây nếu cần
            self.writer.close()
            self.writer = None


    def send_json(self, obj):
        # schedule send on asyncio loop
        if not self.writer:
            messagebox.showinfo('Info', 'Not connected')
            return
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.send_json_async(obj), loop)
            else:
                loop.run_until_complete(self.send_json_async(obj))
        except RuntimeError:
             pass


    # UI helpers
    def set_status(self, s):
        self.status_var.set(s)

    def handle_msg(self, msg):
        t = msg.get('type')
        if t == 'login_ok':
            users = msg.get('users', [])
            self.update_users(users)
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
            self.turn = 'X'
            self.clear_board()
            self.set_status(f'In match vs {self.opponent} you={self.you}')
        elif t == 'your_turn':
            self.turn = self.you
            self.set_status('Your turn')
        elif t == 'opponent_move':
            x = msg.get('x'); y = msg.get('y'); sym = msg.get('symbol')
            self.set_cell(x, y, sym)
            self.turn = self.you
            self.set_status('Your turn')
        elif t == 'move_ok':
            x = msg.get('x'); y = msg.get('y'); sym = msg.get('symbol')
            self.set_cell(x, y, sym)
            self.turn = 'O' if sym == 'X' else 'X'
            self.set_status('Waiting')
        elif t == 'highlight':
            cells = msg.get('cells', [])
            winner = msg.get('winner', '')
            for (x, y) in cells:
                if 0 <= y < BOARD_SIZE and 0 <= x < BOARD_SIZE:
                    self.cells[y][x]['bg'] = 'yellow'
            self.set_status(f'{winner} wins! Highlighting...')
        elif t == 'match_end':
            result = msg.get('result')
            if result == 'win':
                messagebox.showinfo('Kết thúc', 'Bạn đã thắng!')
            elif result == 'lose':
                messagebox.showinfo('Kết thúc', 'Bạn đã thua!')
            else:
                messagebox.showinfo('Kết thúc', 'Trận đấu đã kết thúc.')
            self.in_match = False
            self.you = None
            self.opponent = None
            self.set_status('Idle')
            self.clear_board()

        elif t == 'error':
            messagebox.showerror('Error', msg.get('msg', ''))

    def update_users(self, users):
        self.users_listbox.delete(0, tk.END)
        for u in users:
            self.users_listbox.insert(tk.END, u)

    def clear_board(self):
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                self.cells[y][x]['text'] = ''
                self.cells[y][x]['bg'] = 'SystemButtonFace'

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
        app.on_disconnect() # Đảm bảo ngắt kết nối
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == '__main__':
    main()
