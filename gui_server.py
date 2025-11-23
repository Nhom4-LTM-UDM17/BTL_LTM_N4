import tkinter as tk
from tkinter import messagebox
import threading
import asyncio
from server import CaroServer
from match_viewer import MatchViewer


class GuiServer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Caro Server Control Panel")
        self.root.geometry("900x600")

        self.server = None
        self.server_loop: asyncio.AbstractEventLoop | None = None
        self.server_thread: threading.Thread | None = None

        # ===================== UI =====================

        top = tk.Frame(root)
        top.pack(side="top", fill="x", pady=5)

        self.btn_start = tk.Button(
            top, text="Start Server", width=15,
            bg="#28a745", fg="white",
            command=self.start_server
        )
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = tk.Button(
            top, text="Stop Server", width=15,
            bg="#dc3545", fg="white",
            state="disabled",
            command=self.stop_server
        )
        self.btn_stop.pack(side="left", padx=5)

        self.lbl_status = tk.Label(
            top, text="Status: STOPPED", fg="red",
            font=("Arial", 12, "bold")
        )
        self.lbl_status.pack(side="right", padx=10)

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

        self.matches_list = tk.Listbox(right_frame, font=("Consolas", 11))
        self.matches_list.pack(fill="both", expand=True)

        tk.Button(
            right_frame, text="Watch Match",
            command=self.open_match_viewer
        ).pack(pady=5)

        self.root.after(300, self.update_ui)

    # ===================================================
    # START SERVER
    # ===================================================
    def start_server(self):
        if self.server_thread:
            return

        self.server = CaroServer()

        def run_server():
            try:
                self.server_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.server_loop)
                self.server_loop.run_until_complete(self.server.start())
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print("[SERVER ERROR]", e)
            finally:
                try:
                    if self.server_loop and not self.server_loop.is_closed():
                        self.server_loop.call_soon_threadsafe(self.server_loop.stop)
                        self.server_loop.close()
                except Exception as e:
                    print("[Server Loop Close Error]", e)

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

        self.btn_start["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        self.lbl_status["text"] = "Status: RUNNING"
        self.lbl_status["fg"] = "green"

    # ===================================================
    # STOP SERVER
    # ===================================================
    def stop_server(self):
        if not self.server_loop:
            print("Server loop not initialized.")
            return

        try:
            for task in asyncio.all_tasks(self.server_loop):
                task.cancel()
            self.server_loop.call_soon_threadsafe(self.server_loop.stop)
        except Exception as e:
            print("[Stop Server Error]", e)

        self.server_thread = None
        self.server_loop = None

        self.btn_start["state"] = "normal"
        self.btn_stop["state"] = "disabled"
        self.lbl_status["text"] = "Status: STOPPED"
        self.lbl_status["fg"] = "red"

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
            for name in self.server.clients.keys():
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

            # Restore selection
            if selected_index is not None and selected_index < self.matches_list.size():
                self.matches_list.select_set(selected_index)
                self.matches_list.activate(selected_index)

        self.root.after(300, self.update_ui)

    def open_match_viewer(self):
        sel = self.matches_list.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select a match")
            return

        match_text = self.matches_list.get(sel[0])
        match_id = match_text.split(" | ")[0]

        if match_id not in self.server.matches:
            messagebox.showerror("Error", "Match not found")
            return

        m = self.server.matches[match_id]
        win = tk.Toplevel(self.root)
        viewer = MatchViewer(win, self.server, match_id)


# ===================================================
# MAIN
# ===================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = GuiServer(root)
    root.mainloop()
