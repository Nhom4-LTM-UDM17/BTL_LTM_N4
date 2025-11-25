import tkinter as tk                # Thư viện giao diện GUI chính
from tkinter import messagebox      # Dùng để hiển thị hộp thoại thông báo
import threading                    # Dùng để chạy server trong thread riêng
import asyncio                      # Dùng để chạy server bất đồng bộ (async)
from server import CaroServer          
from match_viewer import MatchViewer

class GuiServer:
    def __init__(self, root: tk.Tk):
        # Lưu lại cửa sổ gốc Tkinter
        self.root = root
        
        # Đặt title cửa sổ
        self.root.title("Caro Server Control Panel")
        
        # Đặt kích thước cửa sổ
        self.root.geometry("900x600")

        # Khai báo biến server và các cấu trúc điều khiển server
        self.server = None      # Lưu instance của CaroServer
        self.server_loop: asyncio.AbstractEventLoop | None = None   # Event loop của asyncio, dùng để chạy server.
        self.server_thread: threading.Thread | None = None          # Thread chạy server, không làm treo giao diện Tkinter.


        # ====================== GIAO DIỆN PHÍA TRÊN =======================

        top = tk.Frame(root)       # Tạo frame bên trên
        top.pack(side="top", fill="x", pady=5)

        # NÚT START SERVER
        self.btn_start = tk.Button(
            top, text="Start Server", width=15,
            bg="#28a745", fg="white",
            command=self.start_server           # Khi click gọi hàm start_server
        )
        self.btn_start.pack(side="left", padx=5)

        # NÚT STOP SERVER
        self.btn_stop = tk.Button(
            top, text="Stop Server", width=15,
            bg="#dc3545", fg="white",
            state="disabled",                  # Mặc định vô hiệu (chưa chạy server)
            command=self.stop_server           # Khi click gọi stop_server
        )
        self.btn_stop.pack(side="left", padx=5)

        # LABEL TRẠNG THÁI SERVER
        self.lbl_status = tk.Label(
            top, text="Status: STOPPED", fg="red",
            font=("Arial", 12, "bold")
        )
        self.lbl_status.pack(side="right", padx=10)
        
        # LABEL "Port:"
        tk.Label(top, text="Port:", font=("Arial", 10)).pack(side="left", padx=(10, 2))
        
        # BIẾN CHỨA PORT
        self.port_var = tk.StringVar(value=str('7777')) # Gán giá trị mặc định
        
        # Ô nhập PORT
        self.entry_port = tk.Entry(
            top, 
            textvariable=self.port_var,     # Liên kết với biến port
            width=10, 
            font=("Arial", 10),
            justify='center'
        )
        self.entry_port.pack(side="left", padx=(0, 10))
        
        # LABEL HOST/IP
        self.lbl_host = tk.Label(
            top, text="Host: ---", fg="black",      # Mặc định chưa có IP
            font=("Arial", 10)
        )
        self.lbl_host.pack(side="left", padx=10)

        # ========== LEFT: Clients ==========
        left_frame = tk.LabelFrame(root, text="Connected Clients", padx=5, pady=5)
        left_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        self.clients_list = tk.Listbox(left_frame, font=("Consolas", 11))
        self.clients_list.pack(fill="both", expand=True)

        self.lbl_clients_count = tk.Label(left_frame, text="Total: 0 users")
        self.lbl_clients_count.pack(anchor="w")

        # ========== RIGHT: Matches ==========
        right_frame = tk.LabelFrame(root, text="Active Matches", padx=5, pady=5)
        right_frame.pack(side="right", fill="both", expand=True, padx=5, pady=5)

        # Danh sách trận đấu
        self.matches_list = tk.Listbox(right_frame, font=("Consolas", 11))
        self.matches_list.pack(fill="both", expand=True)

        # Nút xem trận
        tk.Button(
            right_frame, text="Watch Match",
            command=self.open_match_viewer      # Mở viewer trận đấu
        ).pack(pady=5)

        # Thiết lập cập nhật UI mỗi 300ms
        self.root.after(300, self.update_ui)

    # ===================================================
    # START SERVER
    # ===================================================
    def start_server(self):
        # Lấy Port mới từ ô nhập liệu và kiểm tra
        try:
            new_port = int(self.port_var.get())
            if not (1024 <= new_port <= 65535):
                raise ValueError("Port must be between 1024 and 65535.")
        except ValueError as e:
            messagebox.showerror("Invalid Port", f"Invalid Port number: {e}")
            return

        # 1. Khởi tạo đối tượng CaroServer với Port mới
        try:
            self.server = CaroServer(port=new_port) 
        except Exception as e:
            messagebox.showerror("Error", f"Failed to initialize server: {e}")
            return
        
        local_ip = "127.0.0.1" 
        try:
            # Lấy IP LAN của server
            local_ip = self.server.get_local_ip() 
            self.lbl_host["text"] = f"Host: {local_ip} (LAN)"
            
            # Vô hiệu hóa ô nhập Port khi Server đang chạy
            self.entry_port.config(state='disabled') 
        except Exception as e:
            print(f"[WARN] Failed to get local IP: {e}")

        def run_server():
            try:
                self.server_loop = asyncio.new_event_loop()                 # Tạo event loop mới
                asyncio.set_event_loop(self.server_loop)                    # Gán loop này làm loop chính
                self.server_loop.run_until_complete(self.server.start())    # Chạy server
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print("[SERVER ERROR]", e)
            finally:
                try:
                    # Nếu event loop còn tồn tại và chưa đóng,
                    # ta phải dừng nó theo cách thread-safe
                    if self.server_loop and not self.server_loop.is_closed():
                        # Gửi tín hiệu stop() vào thread của event loop
                        # -> cách duy nhất để dừng loop an toàn khi nó chạy ở thread khác
                        self.server_loop.call_soon_threadsafe(self.server_loop.stop)
                        
                        # Đóng loop để giải phóng tài nguyên
                        self.server_loop.close()
                except Exception as e:
                    print("[Server Loop Close Error]", e)

        # Tạo thread chạy server
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

        # Cập nhật UI khi server bắt đầu chạy
        self.btn_start["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        self.lbl_status["text"] = "Status: RUNNING"
        self.lbl_status["fg"] = "green"

    # ===================================================
    # STOP SERVER
    # ===================================================
    def stop_server(self):
        if not self.server_loop or not self.server:
            print("Server loop or server not initialized.")
            return

        try:
            # Hủy các task đang chạy (ví dụ: timer, broadcast)
            for task in asyncio.all_tasks(self.server_loop):
                task.cancel()
            
            # Gọi server.stop()
            asyncio.run_coroutine_threadsafe(self.server.stop(), self.server_loop)

        except Exception as e:
            print("[Stop Server Error]", e)

        # Reset UI và biến
        # Gọi cleanup UI sau 500ms để thread có thời gian dừng
        self.root.after(500, self._cleanup_after_stop)

    def _cleanup_after_stop(self):
        # Reset trạng thái server
        self.server_thread = None
        self.server_loop = None
        self.server = None

        # Reset giao diện
        self.btn_start["state"] = "normal"
        self.btn_stop["state"] = "disabled"
        self.lbl_status["text"] = "Status: STOPPED"
        self.lbl_status["fg"] = "red"
        self.entry_port.config(state='normal') 
        self.lbl_host["text"] = "Host: ---"

        # Xóa danh sách client và trận đấu
        self.clients_list.delete(0, tk.END)
        self.matches_list.delete(0, tk.END)
        self.lbl_clients_count["text"] = "Total: 0 users"

    # ===================================================
    # UI UPDATE LOOP (KEEP SELECTION)
    # ===================================================
    def update_ui(self):
        if self.server:

            # ============ Update Clients ============
            self.clients_list.delete(0, tk.END)
            for name in list(self.server.clients.keys()):
                self.clients_list.insert(tk.END, name)
            self.lbl_clients_count["text"] = f"Total: {len(self.server.clients)} users"

            # ============ Update Matches ============
            old_selection = self.matches_list.curselection()
            selected_index = old_selection[0] if old_selection else None

            self.matches_list.delete(0, tk.END)
            for m in self.server.matches.values():
                self.matches_list.insert(
                    tk.END,
                    f"{m.id} | {m.player_x} (X) vs {m.player_o} (O) | turn: {m.turn}"
                )

            # Khôi phục item đang được chọn
            if selected_index is not None and selected_index < self.matches_list.size():
                self.matches_list.select_set(selected_index)
                self.matches_list.activate(selected_index)

        # Lặp lại sau 300ms
        self.root.after(300, self.update_ui)


    # ====================================================================
    # HÀM MỞ VIEWER TRẬN ĐẤU
    # ====================================================================
    def open_match_viewer(self):
        sel = self.matches_list.curselection()          # Lấy item được chọn
        if not sel:
            messagebox.showinfo("Info", "Select a match")
            return

        match_text = self.matches_list.get(sel[0])
        match_id = match_text.split(" | ")[0]

        if match_id not in self.server.matches:
            messagebox.showerror("Error", "Match not found")
            return
        
        # Mở cửa sổ xem trận
        win = tk.Toplevel(self.root)
        MatchViewer(win, self.server, match_id)


# ===================================================
# MAIN
# ===================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = GuiServer(root)
    root.mainloop()
