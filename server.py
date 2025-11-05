import asyncio, sqlite3, json, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List

from common import BOARD_SIZE, THINK_TIME_SECONDS, send_json, recv_json, check_win, find_win_line

@dataclass
class Client:
    name: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    in_match: Optional[str] = None

@dataclass
class Match:
    id: str
    player_x: str
    player_o: str
    board: List[List[str]] = field(default_factory=lambda: [["." for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)])
    turn: str = "X"
    started_at: float = field(default_factory=time.time)
    moves: List[Dict] = field(default_factory=list)
    deadline: Optional[float] = None

class CaroServer:
    def __init__(self, host="0.0.0.0", port=7777, db_path="game_history.db"):
        self.host = host
        self.port = port
        self.db = sqlite3.connect(db_path)
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
        self.clients: Dict[str, Client] = {}
        self.matches: Dict[str, Match] = {}
        self.pending_invites: Dict[tuple, bool] = {}

    async def start(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        print(f"Server listening on {self.host}:{self.port}")
        async with server:
            await server.serve_forever()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            msg = await recv_json(reader)
            if msg.get("type") != "login" or not msg.get("name"):
                await send_json(writer, {"type": "error", "msg": "Must login first"})
                writer.close(); await writer.wait_closed(); return
            name = msg["name"].strip()
            if name in self.clients:
                await send_json(writer, {"type": "error", "msg": "Name already in use"})
                writer.close(); await writer.wait_closed(); return
            self.clients[name] = Client(name, reader, writer)
            await send_json(writer, {"type": "login_ok", "users": list(self.clients.keys())})
            await self.broadcast_user_list()
            await self.client_loop(self.clients[name])
        except Exception:
            pass
        finally:
            gone = None
            for n, c in list(self.clients.items()):
                if c.writer is writer:
                    gone = n
                    del self.clients[n]
            await self.broadcast_user_list()
            if gone:
                print(f"{gone} disconnected")

    async def broadcast_user_list(self):
        users = list(self.clients.keys())
        for c in list(self.clients.values()):
            try:
                await send_json(c.writer, {"type": "user_list", "users": users})
            except:
                pass

    async def client_loop(self, client: Client):
        reader, writer = client.reader, client.writer
        while True:
            msg = await recv_json(reader)
            t = msg.get("type")
            if t == "challenge":
                await self.handle_challenge(client, msg.get("opponent"))
            elif t == "accept":
                await self.handle_accept(client, msg.get("opponent"))
            elif t == "move":
                await self.handle_move(client, msg)
            elif t == "chat":
                await self.relay_chat(client, msg.get("text", ""))
            else:
                await send_json(writer, {"type": "error", "msg": "unknown type"})

    async def handle_challenge(self, client: Client, opponent: str | None):
        if not opponent or opponent not in self.clients:
            return await send_json(client.writer, {"type": "error", "msg": "opponent not found"})
        if client.in_match or self.clients[opponent].in_match:
            return await send_json(client.writer, {"type": "error", "msg": "someone already in a match"})
        self.pending_invites[(client.name, opponent)] = True
        await send_json(self.clients[opponent].writer, {"type": "invite", "from": client.name})

    async def handle_accept(self, client: Client, opponent: str | None):
        if not opponent or (opponent, client.name) not in self.pending_invites:
            return await send_json(client.writer, {"type": "error", "msg": "no invite found"})
        del self.pending_invites[(opponent, client.name)]
        match_id = f"M{int(time.time()*1000)}"
        player_x = opponent
        player_o = client.name
        m = Match(match_id, player_x, player_o)
        self.matches[match_id] = m
        self.clients[player_x].in_match = match_id
        self.clients[player_o].in_match = match_id

        await send_json(self.clients[player_x].writer, {
            "type": "match_start", "you": "X", "opponent": player_o, "size": BOARD_SIZE
        })
        await send_json(self.clients[player_o].writer, {
            "type": "match_start", "you": "O", "opponent": player_x, "size": BOARD_SIZE
        })

        # Không bắt đầu timer ở đây
        await send_json(self.clients[player_x].writer, {"type": "your_turn", "deadline": None})


    async def start_turn_timer(self, m: Match):
        cur_name = m.player_x if m.turn == "X" else m.player_o
        cur_client = self.clients.get(cur_name)
        if not cur_client:
            return

        # Reset deadline cho lượt mới
        m.deadline = time.time() + THINK_TIME_SECONDS
        await send_json(cur_client.writer, {
            "type": "your_turn",
            "deadline": int(m.deadline)
        })

        async def timer_task(match_id: str, expected_turn: str, deadline: float):
            await asyncio.sleep(THINK_TIME_SECONDS)
            mm = self.matches.get(match_id)
            if not mm:
                return
            # Kiểm tra lại deadline để đảm bảo không bị lẫn với lượt mới
            if mm.deadline and abs(mm.deadline - deadline) < 0.1 and mm.turn == expected_turn:
                print(f"[TIMEOUT] {expected_turn}'s time expired for match {match_id}")
                winner = mm.player_o if expected_turn == "X" else mm.player_x
                await self.finish_match(mm, winner=winner, reason="timeout")

        asyncio.create_task(timer_task(m.id, m.turn, m.deadline))


    def opponent_of(self, m: Match, name: str) -> str:
        return m.player_o if name == m.player_x else m.player_x

    async def handle_move(self, client: Client, msg: Dict):
        match_id = client.in_match
        if not match_id or match_id not in self.matches:
            return await send_json(client.writer, {"type": "error", "msg": "not in a match"})
        m = self.matches[match_id]
        symbol = "X" if client.name == m.player_x else "O"
        if symbol != m.turn:
            return await send_json(client.writer, {"type": "error", "msg": "not your turn"})
        x, y = msg.get("x"), msg.get("y")
        if not isinstance(x, int) or not isinstance(y, int) or not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return await send_json(client.writer, {"type": "error", "msg": "bad coords"})
        if m.board[y][x] != ".":
            return await send_json(client.writer, {"type": "error", "msg": "occupied"})

        # Đánh nước đi
        m.board[y][x] = symbol
        m.moves.append({"x": x, "y": y, "symbol": symbol, "ts": int(time.time())})

        # Dừng timer hiện tại (reset)
        m.deadline = None

        # Gửi thông báo nước đi cho hai bên
        await send_json(client.writer, {"type": "move_ok", "x": x, "y": y, "symbol": symbol})
        opp = self.clients.get(self.opponent_of(m, client.name))
        if opp:
            await send_json(opp.writer, {"type": "opponent_move", "x": x, "y": y, "symbol": symbol})

        # Kiểm tra thắng
        win_cells = find_win_line(m.board, x, y, symbol)
        if win_cells:
            # Gửi danh sách 5 ô thắng cho cả hai bên để highlight
            for player_name in [m.player_x, m.player_o]:
                c = self.clients.get(player_name)
                if c:
                    await send_json(c.writer, {
                        "type": "highlight",
                        "cells": win_cells,  # danh sách [(x, y), (x, y), ...]
                        "winner": client.name
                    })
            
            # Đợi 3 giây cho hiệu ứng highlight
            await asyncio.sleep(3)
            
            # Gửi kết quả riêng cho từng bên
            for pname in [m.player_x, m.player_o]:
                c = self.clients.get(pname)
                if not c:
                    continue
                if pname == client.name:
                    await send_json(c.writer, {"type": "match_end", "result": "win"})
                else:
                    await send_json(c.writer, {"type": "match_end", "result": "lose"})

            # Sau đó thông báo kết thúc trận
            return await self.finish_match(m, winner=client.name, reason="win")

        # Kiểm tra hòa cờ nếu bàn đã đầy mà không có người thắng
        if all('.' not in row for row in m.board):
            for pname in [m.player_x, m.player_o]:
                c = self.clients.get(pname)
                if c:
                    await send_json(c.writer, {"type": "match_end", "result": "draw"})
            return await self.finish_match(m, winner=None, reason="draw")
        
        # Đổi lượt
        m.turn = "O" if m.turn == "X" else "X"

        # Reset và khởi động thời gian cho người kế tiếp
        await self.start_turn_timer(m)


    async def finish_match(self, m: Match, winner: Optional[str], reason: str):
        for name in [m.player_x, m.player_o]:
            c = self.clients.get(name)
            if c:
                who = "you" if winner == name else ("opponent" if winner else "none")
                await send_json(c.writer, {"type": "match_end", "reason": reason, "winner": who})
                c.in_match = None
        self.save_history(m, winner)
        if m.id in self.matches:
            del self.matches[m.id]

    def save_history(self, m: Match, winner: Optional[str]):
        self.db.execute(
            "INSERT OR REPLACE INTO matches (id, player_x, player_o, winner, started_at, finished_at, moves) VALUES (?,?,?,?,?,?,?)",
            (
                m.id,
                m.player_x,
                m.player_o,
                winner or "none",
                datetime.fromtimestamp(m.started_at).isoformat(timespec="seconds"),
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(m.moves, ensure_ascii=False),
            ),
        )
        self.db.commit()

    async def relay_chat(self, client: Client, text: str):
        match_id = client.in_match
        if not match_id or match_id not in self.matches:
            return
        m = self.matches[match_id]
        opp = self.clients.get(self.opponent_of(m, client.name))
        if opp:
            await send_json(opp.writer, {"type": "chat", "from": client.name, "text": text})

if __name__ == "__main__":
    asyncio.run(CaroServer().start())

