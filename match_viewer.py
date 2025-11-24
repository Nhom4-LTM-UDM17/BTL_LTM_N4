import tkinter as tk
from gui_client import BOARD_SIZE
from common import find_win_line


class MatchViewer:
    def __init__(self, root, server, match_id):
        self.root = root
        self.server = server
        self.match_id = match_id

        self.root.title(f"Watching Match {match_id}")
        self.root.geometry("700x780")
        self.root.config(bg="#1e1e2f")
        self.last_move = None

        # ====================================================
        # HEADER - HIỂN THỊ TÊN NGƯỜI CHƠI + LƯỢT HIỆN TẠI
        # ====================================================
        header = tk.Frame(self.root, bg="#252539", pady=10)
        header.pack(fill="x")

        self.label_title = tk.Label(
            header,
            text="Match Viewer",
            font=("Segoe UI", 13, "bold"),
            bg="#252539",
            fg="#FFD700"
        )
        self.label_title.pack()

        self.label_players = tk.Label(
            header,
            text="Players: --- vs ---",
            font=("Segoe UI", 12),
            bg="#252539",
            fg="white"
        )
        self.label_players.pack()

        self.label_turn = tk.Label(
            header,
            text="Turn: ---",
            font=("Segoe UI", 12, "bold"),
            bg="#252539",
            fg="#00FFAA"
        )
        self.label_turn.pack()

        # ==========================
        # BOARD CANVAS
        # ==========================
        self.canvas = tk.Canvas(self.root, bg="#1e1e2f")
        self.canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.canvas.bind("<Configure>", self.on_resize)

        # ==========================
        # BOARD STATE
        # ==========================
        self.cell_size = 0
        self.offset_x = 0
        self.offset_y = 0
        self.board_state = [["" for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.highlighted = []

        # Start update loop
        self.refresh()

    # ====================================================
    # AUTO REFRESH EACH 250ms
    # ====================================================
    def refresh(self):
        if self.match_id not in self.server.matches:
            return

        m = self.server.matches[self.match_id]

        # ==== Update players ====
        self.label_players.config(
            text=f"{m.player_x} (X)  vs  {m.player_o} (O)"
        )

        # ==== Update turn ====
        turn_text = f"Turn: {m.turn}"
        turn_color = "#FF3B30" if m.turn == "X" else "#00AEEF"
        self.label_turn.config(text=turn_text, fg=turn_color)

        # ==== Update board ====
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                val = m.board[y][x]
                self.board_state[y][x] = "" if val == "." else val

        # ==== Lấy nước đi cuối ====
        if m.moves:
            self.last_move = (m.moves[-1]["x"], m.moves[-1]["y"])
        else:
            self.last_move = None

        # ==================================================================
        #  TÌM NGƯỜI THẮNG TỰ ĐỘNG DỰA TRÊN find_win_line()
        # ==================================================================
        self.highlighted = []

        # Quét toàn bộ bàn cờ tìm đường thắng
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                symbol = self.board_state[y][x]
                if symbol in ("X", "O"):
                    line = find_win_line(self.board_state, x, y, symbol)
                    if len(line) >= 5:
                        self.highlighted = line
                        break
            if self.highlighted:
                break
        
        # ==================================================================
        #  Nếu chưa thắng, highlight last move thôi
        # ==================================================================
        if not self.highlighted and self.last_move:
            lx, ly = self.last_move
            symbol = self.board_state[ly][lx]
            if symbol:
                self.highlighted = find_win_line(self.board_state, lx, ly, symbol)

        # Draw
        self.redraw()
        self.root.after(250, self.refresh)
        

    # ====================================================
    # DRAWING (same logic as client)
    # ====================================================
    def on_resize(self, evt=None):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        padding = 40
        usable_w = w - padding * 2
        usable_h = h - padding * 2

        self.cell_size = min(usable_w, usable_h) // BOARD_SIZE
        if self.cell_size < 5:
            return

        self.offset_x = (w - self.cell_size * BOARD_SIZE) // 2
        self.offset_y = (h - self.cell_size * BOARD_SIZE) // 2

        self.redraw()

    def redraw(self):
        if self.cell_size <= 0:
            return

        self.canvas.delete("all")

        # Grid
        for i in range(BOARD_SIZE + 1):
            x = self.offset_x + i * self.cell_size
            y = self.offset_y + i * self.cell_size
            self.canvas.create_line(
                x, self.offset_y, x, self.offset_y + self.cell_size * BOARD_SIZE,
                fill="#3a3a50"
            )
            self.canvas.create_line(
                self.offset_x, y, self.offset_x + self.cell_size * BOARD_SIZE, y,
                fill="#3a3a50"
            )

        # Pieces
        for y in range(BOARD_SIZE):
            for x in range(BOARD_SIZE):
                s = self.board_state[y][x]
                if s:
                    color = "#FF3B30" if s == "X" else "#0078D7"
                    self.draw_piece(x, y, s, color)

        # Highlight
        self.draw_highlights()

    def draw_piece(self, x, y, symbol, color):
        cs = self.cell_size
        ox = self.offset_x
        oy = self.offset_y

        cx = ox + x * cs + cs // 2
        cy = oy + y * cs + cs // 2

        r = int(cs * 0.35)

        self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=color, outline=""
        )
        self.canvas.create_text(
            cx, cy, text=symbol, fill="white",
            font=("Consolas", int(cs * 0.4), "bold")
        )

    def draw_highlights(self):
        cs = self.cell_size
        ox = self.offset_x
        oy = self.offset_y

        # Nếu có winner → chỉ highlight win_cells (màu vàng)
        if self.highlighted:
            for (x, y) in self.highlighted:
                x1 = ox + x * cs
                y1 = oy + y * cs
                x2 = x1 + cs
                y2 = y1 + cs

                self.canvas.create_rectangle(
                    x1, y1, x2, y2,
                    outline="#FFD700", width=3
                )
            return 
            
        # Highlight last move
        if self.last_move:
            x, y = self.last_move
            x1 = ox + x * cs
            y1 = oy + y * cs
            x2 = x1 + cs
            y2 = y1 + cs
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#00FF00", width=3)
