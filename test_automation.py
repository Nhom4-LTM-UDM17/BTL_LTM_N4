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
    """
    Fixture này sẽ khởi chạy server.py trong một tiến trình riêng
    trước khi các test bắt đầu, và tắt nó đi khi test xong.
    """
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
# HELPER CLASS: TEST CLIENT
# ==========================

class TestClient:
    """Một class helper để quản lý kết nối của một client giả lập."""
    
    def __init__(self, name: str):
        self.name = name
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.is_connected = False

    async def connect(self) -> bool:
        """Kết nối tới server và gửi thông tin login."""
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
        """Chờ và nhận một thông điệp có type cụ thể."""
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
        """Đóng kết nối."""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except: pass
        self.reader = None
        self.writer = None
        self.is_connected = False
        print(f"CLIENT [{self.name}]: Đã ngắt kết nối.")

# ==========================
# TEST CASES
# ==========================

@pytest.mark.asyncio
async def test_challenge_reject_busy(server_process, check): # <-- Thêm check
    """
    Test case: Thử thách đấu với một người đang bận.
    Cường thách đấu Phụng (đang bận đấu với Bảo).
    """
    cuong = TestClient("Cuong")
    phung = TestClient("Phung")
    bao = TestClient("Bao")
    
    try:
        check.is_true(await cuong.connect(), "Cường kết nối thất bại")
        check.is_true(await phung.connect(), "Phụng kết nối thất bại")
        check.is_true(await bao.connect(), "Bảo kết nối thất bại")
        
        # Phụng và Bảo bắt đầu trận đấu
        await phung.send({'type': 'challenge', 'opponent': 'Bao'})
        invite_msg = await bao.async_recv_msg_by_type('invite')
        check.is_not_none(invite_msg, "Bảo không nhận được mời")
        
        await bao.send({'type': 'accept', 'opponent': 'Phung'})
        
        # Đợi cả hai vào trận
        check.is_not_none(
            await phung.async_recv_msg_by_type('match_start'),
            "Phụng không nhận được 'match_start'"
        )
        check.is_not_none(
            await bao.async_recv_msg_by_type('match_start'),
            "Bảo không nhận được 'match_start'"
        )
        print("CLIENT [Phụng, Bảo]: Đã vào trận.")

        # Cường thách đấu Phụng (đang bận)
        print("CLIENT [Cường]: Thách đấu Phụng (đang bận)...")
        await cuong.send({'type': 'challenge', 'opponent': 'Phung'})
        
        # Cường phải nhận được tin nhắn lỗi
        error_msg = await cuong.async_recv_msg_by_type('error')
        check.is_not_none(error_msg, "Cường không nhận được phản hồi lỗi")
        if error_msg:
            check.equal(
                error_msg.get('msg'), 
                'someone already in a match',
                "Nội dung lỗi không đúng"
            )
        print("CLIENT [Cường]: Nhận được lỗi 'someone already in a match' (SUCCESS).")
        
    finally:
        await cuong.close()
        await phung.close()
        await bao.close()


@pytest.mark.asyncio
async def test_challenge_and_game_win(server_process, check): # <-- Thêm check
    """
    Test case: Thách đấu, chấp nhận, và chơi đến khi thắng.
    Cường (X) thắng Phụng (O).
    """
    cuong = TestClient("Cuong") # X
    phung = TestClient("Phung") # O
    
    try:
        check.is_true(await cuong.connect(), "Cường kết nối thất bại")
        check.is_true(await phung.connect(), "Phụng kết nối thất bại")

        # Luồng thách đấu
        print("CLIENT [Cường]: Thách đấu Phụng...")
        await cuong.send({'type': 'challenge', 'opponent': 'Phung'})
        
        invite_msg = await phung.async_recv_msg_by_type('invite')
        check.is_not_none(invite_msg, "Phụng không nhận được mời")
        if invite_msg:
            check.equal(invite_msg.get('from'), 'Cuong', "Lời mời từ sai người")
        print("CLIENT [Phụng]: Nhận được lời mời từ Cường.")

        await phung.send({'type': 'accept', 'opponent': 'Cuong'})
        
        # Kiểm tra bắt đầu trận đấu
        cuong_start = await cuong.async_recv_msg_by_type('match_start')
        phung_start = await phung.async_recv_msg_by_type('match_start')
        
        check.is_not_none(cuong_start, "Cường không nhận được 'match_start'")
        check.is_not_none(phung_start, "Phụng không nhận được 'match_start'")
        
        if cuong_start:
            check.equal(cuong_start.get('you'), 'X', "Cường không phải là X")
        if phung_start:
            check.equal(phung_start.get('you'), 'O', "Phụng không phải là O")
        print("CLIENT [Cường, Phụng]: Trận đấu bắt đầu.")

        # Bắt đầu chơi (Cường X thắng)
        moves = [
            (cuong, 5, 5), (phung, 0, 0),
            (cuong, 6, 6), (phung, 1, 1),
            (cuong, 7, 7), (phung, 2, 2),
            (cuong, 8, 8), (phung, 3, 3),
            (cuong, 9, 9)  # Nước đi chiến thắng
        ]
        
        for i, (player, x, y) in enumerate(moves):
            # Chờ lượt
            check.is_not_none(
                await player.async_recv_msg_by_type('your_turn'),
                f"{player.name} không nhận được 'your_turn' ở lượt {i+1}"
            )
            
            # Gửi nước đi
            await player.send({'type': 'move', 'x': x, 'y': y})
            
            # Nếu chưa thắng, kiểm tra đối thủ nhận được nước đi
            if i < len(moves) - 1:
                opponent = phung if player is cuong else cuong
                check.is_not_none(
                    await opponent.async_recv_msg_by_type('opponent_move'),
                    f"{opponent.name} không nhận được 'opponent_move' ở lượt {i+1}"
                )
        
        print("CLIENT [Cường]: Đánh nước đi chiến thắng...")
        
        # Kiểm tra kết quả
        cuong_hl = await cuong.async_recv_msg_by_type('highlight')
        phung_hl = await phung.async_recv_msg_by_type('highlight')
        check.is_not_none(cuong_hl, "Cường không nhận được highlight")
        check.is_not_none(phung_hl, "Phụng không nhận được highlight")

        cuong_end = await cuong.async_recv_msg_by_type('match_end', timeout=7)
        phung_end = await phung.async_recv_msg_by_type('match_end', timeout=7)
        
        check.is_not_none(cuong_end, "Cường không nhận được 'match_end'")
        check.is_not_none(phung_end, "Phụng không nhận được 'match_end'")
        
        if cuong_end:
            check.equal(cuong_end.get('result'), 'win', "Cường không nhận 'win'")
        if phung_end:
            check.equal(phung_end.get('result'), 'lose', "Phụng không nhận 'lose'")
        
        print("CLIENT [Cường]: Nhận 'win' (SUCCESS).")
        print("CLIENT [Phụng]: Nhận 'lose' (SUCCESS).")

    finally:
        await cuong.close()
        await phung.close()


@pytest.mark.asyncio
async def test_game_timeout(server_process, check): # <-- Thêm check
    """
    Test case: Thua do hết giờ.
    Cường (X) bị timeout, Phụng (O) thắng.
    """
    TIMEOUT_VAL = THINK_TIME_SECONDS if 'THINK_TIME_SECONDS' in globals() else 15

    cuong = TestClient("Cuong_Timeout") # X
    phung = TestClient("Phung_Win_Timeout") # O
    
    try:
        check.is_true(await cuong.connect(), "Cường (timeout) kết nối thất bại")
        check.is_true(await phung.connect(), "Phụng (win) kết nối thất bại")

        # Vào trận nhanh
        await cuong.send({'type': 'challenge', 'opponent': 'Phung_Win_Timeout'})
        await phung.async_recv_msg_by_type('invite')
        await phung.send({'type': 'accept', 'opponent': 'Cuong_Timeout'})
        await cuong.async_recv_msg_by_type('match_start')
        await phung.async_recv_msg_by_type('match_start')
        
        # Đến lượt Cường
        check.is_not_none(
            await cuong.async_recv_msg_by_type('your_turn'),
            "Cường không nhận được 'your_turn'"
        )
        print(f"CLIENT [Cuong_Timeout]: Đến lượt. Bắt đầu chờ {TIMEOUT_VAL + 2} giây...")
        
        # Không làm gì cả, chờ server xử timeout
        await asyncio.sleep(TIMEOUT_VAL + 2)
        
        print("CLIENT: Hết thời gian chờ. Kiểm tra kết quả timeout...")
        
        # --- PHẦN KIỂM TRA ĐÃ SỬA ---
        
        # 1. Kiểm tra Cường (người thua)
        cuong_end = await cuong.async_recv_msg_by_type('match_end', timeout=5)
        check.is_not_none(
            cuong_end, 
            "Cường KHÔNG nhận được tin nhắn 'match_end' (bị timeout 5s)"
        )
        
        # 2. Kiểm tra Phụng (người thắng)
        phung_end = await phung.async_recv_msg_by_type('match_end', timeout=5)
        check.is_not_none(
            phung_end, 
            "Phụng KHÔNG nhận được tin nhắn 'match_end' (bị timeout 5s)"
        )

        # 3. Phân tích kết quả (ĐÃ BỊ XÓA THEO YÊU CẦU)

    finally:
        await cuong.close()
        await phung.close()