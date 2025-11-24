import asyncio
import sqlite3
import json
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
import socket
from typing import Dict, Optional, List
from collections import deque

from common import BOARD_SIZE, THINK_TIME_SECONDS, send_json, recv_json, find_win_line

# ============================================
# CÁC HẰNG SỐ - Kiểu như settings của game
# ============================================
HIGHLIGHT_DELAY = 3.0  # Đợi 3 giây để người chơi ngắm line thắng trước khi kết thúc
RATE_LIMIT_REQUESTS = 20  # Tối đa 20 requests
RATE_LIMIT_WINDOW = 2.0   # Trong 2 giây (chống spam/DoS)
BROADCAST_DEBOUNCE = 0.1  # Debounce 100ms cho broadcast user list

# ============================================
# DATACLASS - Cấu trúc dữ liệu dễ hiểu
# ============================================

@dataclass
class Client:
    """
    Đại diện cho 1 người chơi đang online
    - name: tên hiển thị
    - reader/writer: ống dẫn để gửi/nhận tin nhắn
    - in_match: đang ở trận nào? (None = đang rảnh)
    - request_times: lịch sử request để rate limiting
    """
    name: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    in_match: Optional[str] = None
    request_times: deque = field(default_factory=lambda: deque(maxlen=RATE_LIMIT_REQUESTS))

@dataclass
class Match:
    """
    Một trận đấu đang diễn ra
    - id: mã trận (M + timestamp)
    - player_x/player_o: ai cầm X, ai cầm O
    - board: bàn cờ 15x15, "." = ô trống
    - turn: lượt của ai ("X" hoặc "O")
    - moves: lịch sử các nước đi (để lưu database sau)
    - deadline: hết giờ lúc nào?
    - timer_task: cái đồng hồ đếm ngược đang chạy
    - is_finishing: flag để tránh race condition khi finish_match được gọi nhiều lần
    """
    id: str
    player_x: str
    player_o: str
    board: List[List[str]] = field(default_factory=lambda: [["." for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)])
    turn: str = "X"
    started_at: float = field(default_factory=time.time)
    moves: List[Dict] = field(default_factory=list)
    deadline: Optional[float] = None
    timer_task: Optional[asyncio.Task] = None
    is_finishing: bool = False  # Cờ để tránh race condition

# ============================================
# SERVER CHÍNH
# ============================================

class CaroServer:
    def __init__(self, host="0.0.0.0", port=7777, db_path="game_history.db"):
        """
        Khởi tạo server - như mở cửa hàng cờ
        - Kết nối database để lưu lịch sử (thread-safe với lock)
        - Tạo bảng matches nếu chưa có
        - Chuẩn bị 3 dictionary để quản lý:
          + clients: danh sách người online
          + matches: các trận đang đấu
          + pending_invites: lời mời đang chờ (A thách B)
        """
        self.host = host
        self.port = port
        print(f"[INFO] Connecting to database: {db_path}")
        
        # Kết nối database với thread safety
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db_lock = threading.Lock()  # Lock để đảm bảo thread-safe
        print("[INFO] Database connected successfully")
        
        # Tạo bảng lưu lịch sử nếu chưa có
        with self.db_lock:
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
                    player_x TEXT,
                    player_o TEXT,
                    winner TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    moves TEXT
                )
                """
            )
            self.db.commit()
        
        # 3 bộ não của server
        self.clients: Dict[str, Client] = {}  # Ai đang online?
        self.matches: Dict[str, Match] = {}   # Trận nào đang đấu?
        self.pending_invites: Dict[tuple, bool] = {}  # Lời mời nào đang chờ?
        
        # Cache để tối ưu broadcast
        self.last_user_list: List[str] = []
        self.broadcast_task: Optional[asyncio.Task] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def __del__(self):
        """Dọn dẹp khi tắt server - đóng database cho sạch"""
        if hasattr(self, 'db'):
            with self.db_lock:
                self.db.close()
            print("[INFO] Database connection closed")
            
    def get_local_ip(self):
        """Lấy IP nội bộ của máy (LAN IP)"""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # kết nối giả để router cấp IP
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    async def start(self):
        """
        Khởi động server - mở cửa đón khách
        Lắng nghe ở port 7777, mỗi người vào sẽ gọi handle_client
        """
        self.loop = asyncio.get_event_loop()
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        
        # Lấy IP thật của máy để hiển thị
        local_ip = self.get_local_ip()
        print(f"[INFO] Server listening on {self.host}:{self.port}")
        print(f"[INFO] LAN IP address: {local_ip}:{self.port}")
        async with self.server:
            await self.server.serve_forever()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """
        Xử lý 1 người chơi từ khi vào đến khi thoát
        Flow: Login -> Chơi game -> Disconnect -> Cleanup
        """
        addr = writer.get_extra_info('peername')
        client_name = None
        try:
            # BƯỚC 1: Đợi người chơi login
            msg = await recv_json(reader)
            
            # Bắt buộc phải login trước
            if msg.get("type") != "login" or not msg.get("name"):
                await send_json(writer, {"type": "error", "msg": "Must login first"})
                writer.close()
                await writer.wait_closed()
                return
            
            # Kiểm tra tên hợp lệ (1-50 ký tự, không rỗng)
            name = msg["name"].strip()
            if not name or len(name) > 50:
                await send_json(writer, {"type": "error", "msg": "Invalid name (1-50 characters)"})
                writer.close()
                await writer.wait_closed()
                return
            
            # Kiểm tra tên có bị trùng không
            if name in self.clients:
                await send_json(writer, {"type": "error", "msg": "Name already in use"})
                writer.close()
                await writer.wait_closed()
                return
            
            # BƯỚC 2: ĐĂNG KÝ THÀNH CÔNG!
            client_name = name
            self.clients[name] = Client(name, reader, writer)
            print(f"[INFO] {name} connected from {addr}")
            
            # Gửi danh sách người online cho người mới
            await send_json(writer, {"type": "login_ok", "users": list(self.clients.keys())})
            # Thông báo cho tất cả người khác: có người vừa vào
            await self.broadcast_user_list()
            
            # BƯỚC 3: Vào vòng lặp chính - đợi lệnh từ client
            await self.client_loop(self.clients[name])
            
        except asyncio.CancelledError:
            print(f"[INFO] Client connection cancelled: {client_name}")
        except ConnectionError as e:
            print(f"[INFO] Connection error for {client_name}: {e}")
        except Exception as e:
            print(f"[ERROR] Error handling client {client_name}: {e}")
        finally:
            # BƯỚC 4: Cleanup - dọn dẹp khi disconnect
            if client_name and client_name in self.clients:
                client = self.clients[client_name]
                
                # Nếu đang đánh giữa chừng thì đối thủ thắng luôn
                if client.in_match:
                    match = self.matches.get(client.in_match)
                    if match:
                        # Tắt đồng hồ đếm ngược
                        if match.timer_task and not match.timer_task.done():
                            match.timer_task.cancel()
                            match.timer_task = None
                        
                        # Người còn lại tự động thắng
                        opponent_name = self.opponent_of(match, client_name)
                        print(f"[INFO] {client_name} disconnected during match, {opponent_name} wins")
                        await self.finish_match(match, winner=opponent_name, reason="disconnect")
                
                # Xóa các lời mời đang chờ liên quan đến người này
                keys_to_remove = [key for key in self.pending_invites.keys() if client_name in key]
                for key in keys_to_remove:
                    del self.pending_invites[key]
                
                # Xóa khỏi danh sách online
                del self.clients[client_name]
                print(f"[INFO] {client_name} disconnected")
            
            # Đóng kết nối
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
            
            # Cập nhật danh sách cho người khác
            await self.broadcast_user_list()

    async def broadcast_user_list(self):
        """
        Gửi danh sách người online cho TẤT CẢ mọi người
        Như kiểu cập nhật bảng xếp hạng real-time
        
        Tối ưu:
        - Debounce: chỉ gửi 1 lần trong 100ms
        - Chỉ gửi khi có thay đổi
        - Gửi song song (gather) thay vì tuần tự
        """
        # Cancel broadcast task cũ nếu có
        if self.broadcast_task and not self.broadcast_task.done():
            self.broadcast_task.cancel()
        
        async def _do_broadcast():
            try:
                # Debounce: đợi 100ms để gộp nhiều thay đổi
                await asyncio.sleep(BROADCAST_DEBOUNCE)
                
                users = list(self.clients.keys())
                
                # Chỉ gửi khi có thay đổi
                if users == self.last_user_list:
                    return
                
                self.last_user_list = users.copy()
                
                # Gửi song song cho tất cả client (nhanh hơn vòng for)
                tasks = []
                for c in self.clients.values():
                    tasks.append(send_json(c.writer, {"type": "user_list", "users": users}))
                
                # Chờ tất cả gửi xong, bỏ qua lỗi
                await asyncio.gather(*tasks, return_exceptions=True)
                
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[ERROR] Broadcast error: {e}")
        
        self.broadcast_task = asyncio.create_task(_do_broadcast())

    async def client_loop(self, client: Client):
        """
        Vòng lặp chính - đợi và xử lý lệnh từ client
        Như receptionist nghe điện thoại và điều phối
        
        Bổ sung: Rate limiting để chống DoS attack
        """
        reader, writer = client.reader, client.writer
        
        while True:
            msg = await recv_json(reader)
            
            # RATE LIMITING: Chống spam/DoS
            now = time.time()
            client.request_times.append(now)
            
            # Nếu gửi quá 20 request trong 2 giây → từ chối
            if len(client.request_times) >= RATE_LIMIT_REQUESTS:
                if now - client.request_times[0] < RATE_LIMIT_WINDOW:
                    await send_json(writer, {
                        "type": "error", 
                        "msg": "Rate limit exceeded. Please slow down."
                    })
                    await asyncio.sleep(1.0)  # Phạt đợi 1 giây
                    continue
            
            t = msg.get("type")
            
            # Xử lý từng loại lệnh
            if t == "challenge":
                # "Tôi muốn thách đấu người X"
                await self.handle_challenge(client, msg.get("opponent"))
            elif t == "accept":
                # "Tôi chấp nhận lời thách đấu từ Y"
                await self.handle_accept(client, msg.get("opponent"))
            elif t == "move":
                # "Tôi đánh vào ô (x, y)"
                await self.handle_move(client, msg)
            elif t == "chat":
                # "Tôi muốn chat với đối thủ"
                text = msg.get("text", "").strip()
                if text and len(text) <= 500:  # Max 500 ký tự
                    await self.relay_chat(client, text)
            elif t == "timeout":
                # Client tự báo: "Tôi hết giờ rồi"
                await self.handle_client_timeout(client)
            else:
                await send_json(writer, {"type": "error", "msg": "unknown type"})

    async def handle_challenge(self, client: Client, opponent: str | None):
        """
        Xử lý khi A muốn thách đấu B
        Kiểm tra đủ điều kiện -> gửi lời mời cho B
        """
        # Validate: đối thủ có tồn tại không?
        if not opponent or opponent not in self.clients:
            return await send_json(client.writer, {"type": "error", "msg": "Opponent not found"})
        
        # Không tự thách bản thân
        if opponent == client.name:
            return await send_json(client.writer, {"type": "error", "msg": "Cannot challenge yourself"})
        
        # Bạn đang đấu rồi
        if client.in_match:
            return await send_json(client.writer, {"type": "error", "msg": "You are already in a match"})
        
        # Đối thủ đang đấu với người khác
        if self.clients[opponent].in_match:
            return await send_json(client.writer, {"type": "error", "msg": "Opponent is already in a match"})
        
        # Đã gửi lời mời rồi
        if (client.name, opponent) in self.pending_invites:
            return await send_json(client.writer, {"type": "error", "msg": "Challenge already sent"})
        
        # OK! Lưu lời mời vào hàng đợi
        self.pending_invites[(client.name, opponent)] = True
        print(f"[INFO] {client.name} challenged {opponent}")
        
        # Gửi thông báo cho đối thủ: "X muốn thách bạn"
        await send_json(self.clients[opponent].writer, {"type": "invite", "from": client.name})
        # Thông báo lại cho người gửi: "Đã gửi lời mời"
        await send_json(client.writer, {"type": "challenge_sent", "to": opponent})

    async def handle_accept(self, client: Client, opponent: str | None):
        """
        B chấp nhận thách đấu từ A -> BẮT ĐẦU TRẬN ĐẤU!
        """
        # Kiểm tra có lời mời nào không
        if not opponent or (opponent, client.name) not in self.pending_invites:
            return await send_json(client.writer, {"type": "error", "msg": "No invite found"})
        
        # Người thách còn online không?
        if opponent not in self.clients:
            return await send_json(client.writer, {"type": "error", "msg": "Challenger is offline"})
        
        # Cả 2 đều đang rảnh chứ?
        if client.in_match or self.clients[opponent].in_match:
            return await send_json(client.writer, {"type": "error", "msg": "Someone is already in a match"})
        
        # Xóa lời mời khỏi hàng đợi
        del self.pending_invites[(opponent, client.name)]
        
        # TẠO TRẬN ĐẤU MỚI!
        match_id = f"M{int(time.time()*1000)}"  # ID duy nhất
        player_x = opponent  # Người thách cầm X
        player_o = client.name  # Người chấp nhận cầm O
        m = Match(match_id, player_x, player_o)
        self.matches[match_id] = m
        
        # Đánh dấu cả 2 đang trong trận
        self.clients[player_x].in_match = match_id
        self.clients[player_o].in_match = match_id
        
        print(f"[INFO] Match started: {match_id} - {player_x} vs {player_o}")
        
        # Thông báo cho cả 2: "Trận đấu bắt đầu!"
        await send_json(self.clients[player_x].writer, {
            "type": "match_start", "you": "X", "opponent": player_o, "size": BOARD_SIZE
        })
        await send_json(self.clients[player_o].writer, {
            "type": "match_start", "you": "O", "opponent": player_x, "size": BOARD_SIZE
        })
        
        # Khởi động đồng hồ đếm ngược cho X (đi trước)
        await self.start_turn_timer(m)

    async def start_turn_timer(self, m: Match):
        """
        Bật đồng hồ đếm ngược cho lượt hiện tại
        Hết giờ -> tự động thua
        """
        # Hủy timer cũ nếu có (phòng trường hợp bug)
        if m.timer_task and not m.timer_task.done():
            m.timer_task.cancel()
            m.timer_task = None
        
        # Tìm người đang đến lượt
        cur_name = m.player_x if m.turn == "X" else m.player_o
        cur_client = self.clients.get(cur_name)
        if not cur_client:
            return
        
        # Tính deadline = giờ hiện tại + 30 giây
        m.deadline = time.time() + THINK_TIME_SECONDS
        
        # Thông báo: "Đến lượt bạn, hết giờ lúc..."
        try:
            await send_json(cur_client.writer, {
                "type": "your_turn",
                "deadline": int(m.deadline)
            })
        except Exception as e:
            print(f"[ERROR] Failed to send your_turn to {cur_name}: {e}")
            return
        
        # Tạo task đếm ngược
        async def timer_task():
            try:
                await asyncio.sleep(THINK_TIME_SECONDS)  # Đợi 30 giây
                
                # Kiểm tra trận còn tồn tại không
                if m.id not in self.matches:
                    return
                
                current_match = self.matches[m.id]
                
                # Kiểm tra đúng người, đúng lượt (tránh race condition)
                if (current_match.deadline and 
                    abs(current_match.deadline - m.deadline) < 0.1 and
                    current_match.turn == m.turn and
                    not current_match.is_finishing):
                    
                    # HẾT GIỜ! Đối thủ thắng
                    print(f"[INFO] Timeout: {m.turn} in match {m.id}")
                    winner = m.player_o if m.turn == "X" else m.player_x
                    await self.finish_match(current_match, winner=winner, reason="timeout")
                    
            except asyncio.CancelledError:
                pass  # Timer bị hủy - bình thường thôi
            except Exception as e:
                print(f"[ERROR] Timer task error: {e}")
        
        # Lưu task để có thể hủy sau
        m.timer_task = asyncio.create_task(timer_task())

    def opponent_of(self, m: Match, name: str) -> str:
        """Helper: Tìm đối thủ của name trong trận m"""
        return m.player_o if name == m.player_x else m.player_x

    async def handle_move(self, client: Client, msg: Dict):
        """
        Xử lý nước đi: Client đánh vào ô (x, y)
        Flow: Validate -> Cập nhật board -> Check win -> Chuyển lượt
        
        Cải tiến: Validate input nghiêm ngặt để tránh crash
        """
        # Kiểm tra đang trong trận không
        match_id = client.in_match
        if not match_id or match_id not in self.matches:
            return await send_json(client.writer, {"type": "error", "msg": "Not in a match"})
        
        m = self.matches[match_id]
        symbol = "X" if client.name == m.player_x else "O"
        
        # Đến lượt bạn chưa?
        if symbol != m.turn:
            return await send_json(client.writer, {"type": "error", "msg": "Not your turn"})
        
        # VALIDATE INPUT NGHIÊM NGẶT (chống malicious client)
        try:
            x = int(msg.get("x"))
            y = int(msg.get("y"))
            if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
                raise ValueError("Out of range")
        except (TypeError, ValueError) as e:
            return await send_json(client.writer, {
                "type": "error", 
                "msg": f"Invalid coordinates: {e}"
            })
        
        # Ô đó trống không?
        if m.board[y][x] != ".":
            return await send_json(client.writer, {"type": "error", "msg": "Cell occupied"})
        
        # HỦY TIMER - đã đi rồi!
        if m.timer_task and not m.timer_task.done():
            m.timer_task.cancel()
            m.timer_task = None
        
        # CẬP NHẬT BÀN CỜ
        m.board[y][x] = symbol
        m.moves.append({"x": x, "y": y, "symbol": symbol, "ts": int(time.time())})
        m.deadline = None
        
        print(f"[INFO] Move: {client.name} ({symbol}) -> ({x}, {y})")
        
        # Gửi xác nhận cho người đánh
        await send_json(client.writer, {"type": "move_ok", "x": x, "y": y, "symbol": symbol})
        
        # Thông báo cho đối thủ
        opp = self.clients.get(self.opponent_of(m, client.name))
        if opp:
            await send_json(opp.writer, {"type": "opponent_move", "x": x, "y": y, "symbol": symbol})
        
        # KIỂM TRA THẮNG
        win_cells = find_win_line(m.board, x, y, symbol)
        if win_cells:
            # Highlight line thắng cho cả 2 người
            for player_name in [m.player_x, m.player_o]:
                c = self.clients.get(player_name)
                if c:
                    await send_json(c.writer, {
                        "type": "highlight",
                        "cells": win_cells,
                        "winner": client.name
                    })
            
            # Đợi 3 giây cho họ ngắm
            await asyncio.sleep(HIGHLIGHT_DELAY)
            return await self.finish_match(m, winner=client.name, reason="win")
        
        # KIỂM TRA HÒA (bàn cờ đầy)
        if all(cell != "." for row in m.board for cell in row):
            return await self.finish_match(m, winner=None, reason="draw")
        
        # CHUYỂN LƯỢT
        m.turn = "O" if m.turn == "X" else "X"
        await self.start_turn_timer(m)

    async def handle_client_timeout(self, client: Client):
        """Client tự báo: "Tôi hết giờ rồi" """
        match_id = client.in_match
        if not match_id or match_id not in self.matches:
            return
        
        m = self.matches[match_id]
        symbol = "X" if client.name == m.player_x else "O"
        
        if symbol == m.turn and not m.is_finishing:
            # Đúng là lượt của họ -> đối thủ thắng
            opponent_name = self.opponent_of(m, client.name)
            print(f"[INFO] {client.name} self-reported timeout")
            await self.finish_match(m, winner=opponent_name, reason="timeout")

    async def finish_match(self, m: Match, winner: Optional[str], reason: str):
        """
        KẾT THÚC TRẬN ĐẤU
        Gửi thông báo -> Lưu database -> Dọn dẹp
        
        Cải tiến: Dùng flag is_finishing để tránh race condition
        """
        # CRITICAL: Tránh race condition - chỉ cho phép finish 1 lần
        if m.is_finishing:
            return
        m.is_finishing = True
        
        # Tắt timer
        if m.timer_task and not m.timer_task.done():
            m.timer_task.cancel()
            m.timer_task = None
        
        print(f"[INFO] Match finished: {m.id} - Winner: {winner or 'draw'} ({reason})")
        
        # Gửi kết quả cho cả 2 người
        for name in [m.player_x, m.player_o]:
            c = self.clients.get(name)
            if not c:
                continue
            
            try:
                if winner is None:
                    # HÒA
                    await send_json(c.writer, {
                        "type": "match_end",
                        "result": "draw",
                        "reason": reason,
                        "winner": "none"
                    })
                else:
                    # THẮNG hoặc THUA
                    if name == winner:
                        await send_json(c.writer, {
                            "type": "match_end",
                            "result": "win",
                            "reason": reason,
                            "winner": "you"
                        })
                    else:
                        await send_json(c.writer, {
                            "type": "match_end",
                            "result": "lose",
                            "reason": reason,
                            "winner": "opponent"
                        })
            except Exception as e:
                print(f"[ERROR] Failed to send match_end to {name}: {e}")
            
            # Đánh dấu không còn trong trận
            c.in_match = None
        
        # Lưu vào database (thread-safe)
        self.save_history(m, winner)
        
        # Xóa trận khỏi bộ nhớ
        if m.id in self.matches:
            del self.matches[m.id]

    def save_history(self, m: Match, winner: Optional[str]):
        """
        Lưu lịch sử trận đấu vào SQLite
        
        Cải tiến: Dùng lock để đảm bảo thread-safe
        """
        try:
            with self.db_lock:  # Thread-safe database access
                self.db.execute(
                    "INSERT OR REPLACE INTO matches (id, player_x, player_o, winner, started_at, finished_at, moves) VALUES (?,?,?,?,?,?,?)",
                    (
                        m.id,
                        m.player_x,
                        m.player_o,
                        winner or "draw",
                        datetime.fromtimestamp(m.started_at).isoformat(timespec="seconds"),
                        datetime.now().isoformat(timespec="seconds"),
                        json.dumps(m.moves, ensure_ascii=False),
                    ),
                )
                self.db.commit()
            print(f"[INFO] Match saved: {m.id}")
        except Exception as e:
            print(f"[ERROR] Failed to save match history: {e}")

    async def relay_chat(self, client: Client, text: str):
        """Chuyển tin nhắn chat từ A sang B"""
        match_id = client.in_match
        if not match_id or match_id not in self.matches:
            return
        
        m = self.matches[match_id]
        opp = self.clients.get(self.opponent_of(m, client.name))
        if opp:
            try:
                await send_json(opp.writer, {"type": "chat", "from": client.name, "text": text})
            except Exception as e:
                print(f"[ERROR] Failed to relay chat: {e}")

    async def stop(self):
        """
        Ngừng server, ngắt kết nối tất cả client và dọn dẹp trạng thái.
        Đây là phương thức async, sẽ được gọi từ thread chính của GUI.
        """
        print("[INFO] Shutting down server and forcing client disconnects...")
        
        # 1. Đóng server listener (ngừng chấp nhận client mới)
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        # 2. Đóng tất cả client connections
        tasks = []
        # Dùng list() để duyệt an toàn vì self.clients có thể bị thay đổi nếu client tự disconnect
        for name, client in list(self.clients.items()): 
            if client.writer and not client.writer.is_closing():
                try:
                    client.writer.close()
                    tasks.append(client.writer.wait_closed())
                except Exception as e:
                    print(f"[WARN] Error closing writer for {name}: {e}")
        
        # Chờ tất cả writer đóng (bỏ qua lỗi)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        # 3. Dọn dẹp trạng thái
        self.clients.clear()
        self.matches.clear()
        self.pending_invites.clear()
        
        print("[INFO] Server fully stopped. All clients disconnected.")
        
if __name__ == "__main__":
    try:
        asyncio.run(CaroServer().start())
    except KeyboardInterrupt:
        print("\n[INFO] Server shutting down...")
