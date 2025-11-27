import asyncio
import sys
import pytest
import pytest_asyncio
from typing import Dict, Any, Optional

# Đảm bảo common.py có trong sys.path
try:
    from common import send_json, recv_json, THINK_TIME_SECONDS
except ImportError:
    print("Không tìm thấy file common.py. Hãy chắc chắn nó ở cùng thư mục.")
    sys.exit(1)

HOST = '127.0.0.1'
PORT = 7777

# ==========================
# FIXTURE: TỰ ĐỘNG CHẠY SERVER
# ==========================

@pytest_asyncio.fixture(scope="module")
async def server_process():
    print("\nStarting server...")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "server.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    await asyncio.sleep(1.5) 
    print(f"Server started (PID: {process.pid})")
    yield process
    
    print("\nStopping server...")
    if process.returncode is None:
        process.terminate()
        await process.wait()
    print("Server stopped.")

# ==========================
# HELPER CLASS
# ==========================

class TestClient:
    def __init__(self, name: str):
        self.name = name
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.is_connected = False

    async def connect(self) -> bool:
        try:
            self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
            await send_json(self.writer, {'type': 'login', 'name': self.name})
            response = await recv_json(self.reader)
            
            if response.get('type') == 'login_ok':
                self.is_connected = True
                print(f"CLIENT [{self.name}]: Đăng nhập thành công.")
                return True
            else:
                print(f"CLIENT [{self.name}]: Đăng nhập thất bại: {response.get('msg')}")
                await self.close()
                return False
        except Exception as e:
            print(f"CLIENT [{self.name}]: Lỗi khi kết nối: {e}")
            self.is_connected = False
            return False

    async def send(self, data: Dict[str, Any]):
        if self.writer:
            await send_json(self.writer, data)

    async def recv(self) -> Dict[str, Any]:
        if self.reader:
            return await recv_json(self.reader)
        return {}

    async def async_recv_msg_by_type(self, msg_type: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        try:
            async def _waiter():
                while self.is_connected:
                    msg = await self.recv()
                    if msg.get('type') == msg_type:
                        return msg
                    if msg.get('type') == 'user_list':
                        continue
                return None
            
            return await asyncio.wait_for(_waiter(), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"CLIENT [{self.name}]: Timeout khi chờ tin nhắn '{msg_type}'")
            return None
        except ConnectionError:
            print(f"CLIENT [{self.name}]: Mất kết nối khi chờ '{msg_type}'")
            return None

    async def close(self):
        if self.writer:
            self.writer.close()
            try: await self.writer.wait_closed()
            except: pass
        self.reader = None
        self.writer = None
        self.is_connected = False
        print(f"CLIENT [{self.name}]: Đã ngắt kết nối.")

# ==========================
# TEST CASES
# ==========================

@pytest.mark.asyncio
async def test_challenge_reject_busy(server_process, check):
    """Test: Thử thách đấu với người đang bận."""
    cuong = TestClient("Cuong")
    phung = TestClient("Phung")
    bao = TestClient("Bao")
    
    try:
        check.is_true(await cuong.connect(), "Cường kết nối thất bại")
        check.is_true(await phung.connect(), "Phụng kết nối thất bại")
        check.is_true(await bao.connect(), "Bảo kết nối thất bại")
        
        # Phụng vs Bảo vào trận
        await phung.send({'type': 'challenge', 'opponent': 'Bao'})
        await bao.async_recv_msg_by_type('invite')
        await bao.send({'type': 'accept', 'opponent': 'Phung'})
        
        # Chờ vào trận
        await phung.async_recv_msg_by_type('match_start')
        await bao.async_recv_msg_by_type('match_start')

        # Cường thách đấu Phụng (đang bận)
        await cuong.send({'type': 'challenge', 'opponent': 'Phung'})
        
        # Cường phải nhận lỗi
        error_msg = await cuong.async_recv_msg_by_type('error')
        check.is_not_none(error_msg, "Cường không nhận được lỗi")
        if error_msg:
            check.is_in('already in a match', error_msg.get('msg', ''), "Sai nội dung lỗi")
            
    finally:
        await cuong.close()
        await phung.close()
        await bao.close()

@pytest.mark.asyncio
async def test_game_win_logic(server_process, check):
    """Test: Đánh thắng 5 quân."""
    p1 = TestClient("Winner")
    p2 = TestClient("Loser")
    
    try:
        await p1.connect()
        await p2.connect()
        
        await p1.send({'type': 'challenge', 'opponent': 'Loser'})
        await p2.async_recv_msg_by_type('invite')
        await p2.send({'type': 'accept', 'opponent': 'Winner'})
        
        await p1.async_recv_msg_by_type('match_start')
        await p2.async_recv_msg_by_type('match_start')
        
        # P1 đi trước (X) thắng nhanh
        moves = [
            (p1, 0, 0), (p2, 0, 1),
            (p1, 1, 0), (p2, 1, 1),
            (p1, 2, 0), (p2, 2, 1),
            (p1, 3, 0), (p2, 3, 1),
            (p1, 4, 0) # Thắng
        ]
        
        for player, x, y in moves:
            await player.async_recv_msg_by_type('your_turn')
            await player.send({'type': 'move', 'x': x, 'y': y})
            
        # Kiểm tra kết quả
        win_msg = await p1.async_recv_msg_by_type('match_end', timeout=5)
        lose_msg = await p2.async_recv_msg_by_type('match_end', timeout=5)
        
        check.is_not_none(win_msg, "P1 không nhận được tin thắng")
        check.is_not_none(lose_msg, "P2 không nhận được tin thua")
        
    finally:
        await p1.close()
        await p2.close()

@pytest.mark.asyncio
async def test_game_timeout_first_move(server_process, check):
    """
    Test: Timeout ngay nước đầu tiên (Edge case).
    Cường vào trận rồi không làm gì cả.
    """
    TIMEOUT = THINK_TIME_SECONDS if 'THINK_TIME_SECONDS' in globals() else 15
    p1 = TestClient("TimeoutGuy")
    p2 = TestClient("LuckyGuy")
    
    try:
        await p1.connect()
        await p2.connect()
        
        await p1.send({'type': 'challenge', 'opponent': 'LuckyGuy'})
        await p2.async_recv_msg_by_type('invite')
        await p2.send({'type': 'accept', 'opponent': 'TimeoutGuy'})
        
        await p1.async_recv_msg_by_type('match_start')
        await p2.async_recv_msg_by_type('match_start')
        
        # P1 nhận lượt nhưng không đi
        await p1.async_recv_msg_by_type('your_turn')
        print(f"CLIENT [TimeoutGuy]: Treo máy chờ {TIMEOUT+2}s...")
        await asyncio.sleep(TIMEOUT + 2)
        
        # Kiểm tra
        end1 = await p1.async_recv_msg_by_type('match_end', timeout=5)
        end2 = await p2.async_recv_msg_by_type('match_end', timeout=5)
        
        check.is_not_none(end1, "TimeoutGuy không nhận được match_end (Lỗi timer khởi động)")
        check.is_not_none(end2, "LuckyGuy không nhận được match_end")
        
    finally:
        await p1.close()
        await p2.close()

@pytest.mark.asyncio
async def test_game_timeout_mid_match(server_process, check):
    """
    Test: Timeout giữa trận (Giống test thủ công).
    Hai bên đánh vài nước rồi mới treo máy.
    """
    TIMEOUT = THINK_TIME_SECONDS if 'THINK_TIME_SECONDS' in globals() else 15
    p1 = TestClient("MidGameAFK") # X
    p2 = TestClient("MidGameWin") # O
    
    try:
        await p1.connect()
        await p2.connect()
        
        # Vào trận
        await p1.send({'type': 'challenge', 'opponent': 'MidGameWin'})
        await p2.async_recv_msg_by_type('invite')
        await p2.send({'type': 'accept', 'opponent': 'MidGameAFK'})
        
        await p1.async_recv_msg_by_type('match_start')
        await p2.async_recv_msg_by_type('match_start')
        
        # 1. P1 đánh nước đầu
        await p1.async_recv_msg_by_type('your_turn')
        await p1.send({'type': 'move', 'x': 5, 'y': 5})
        
        # 2. P2 đánh trả
        await p2.async_recv_msg_by_type('your_turn')
        await p2.send({'type': 'move', 'x': 6, 'y': 6})
        
        # 3. P1 đến lượt nhưng treo máy
        await p1.async_recv_msg_by_type('your_turn')
        print(f"CLIENT [MidGameAFK]: Đã đánh 1 nước, giờ treo máy chờ {TIMEOUT+2}s...")
        await asyncio.sleep(TIMEOUT + 2)
        
        # Kiểm tra kết quả
        end1 = await p1.async_recv_msg_by_type('match_end', timeout=5)
        end2 = await p2.async_recv_msg_by_type('match_end', timeout=5)
        
        check.is_not_none(end1, "MidGameAFK không nhận được match_end")
        check.is_not_none(end2, "MidGameWin không nhận được match_end")
        
        if end1:
            check.equal(end1.get('reason'), 'timeout', "Lý do kết thúc không phải timeout")
        
    finally:
        await p1.close()
        await p2.close()