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
    """
    alice = TestClient("Alice")
    bob = TestClient("Bob")
    charlie = TestClient("Charlie")
    
    try:
        check.is_true(await alice.connect(), "Alice kết nối thất bại")
        check.is_true(await bob.connect(), "Bob kết nối thất bại")
        check.is_true(await charlie.connect(), "Charlie kết nối thất bại")
        
        # Bob và Charlie bắt đầu trận đấu
        await bob.send({'type': 'challenge', 'opponent': 'Charlie'})
        invite_msg = await charlie.async_recv_msg_by_type('invite')
        check.is_not_none(invite_msg, "Charlie không nhận được mời")
        
        await charlie.send({'type': 'accept', 'opponent': 'Bob'})
        
        # Đợi cả hai vào trận
        check.is_not_none(
            await bob.async_recv_msg_by_type('match_start'),
            "Bob không nhận được 'match_start'"
        )
        check.is_not_none(
            await charlie.async_recv_msg_by_type('match_start'),
            "Charlie không nhận được 'match_start'"
        )
        print("CLIENT [Bob, Charlie]: Đã vào trận.")

        # Alice thách đấu Bob (đang bận)
        print("CLIENT [Alice]: Thách đấu Bob (đang bận)...")
        await alice.send({'type': 'challenge', 'opponent': 'Bob'})
        
        # Alice phải nhận được tin nhắn lỗi
        error_msg = await alice.async_recv_msg_by_type('error')
        check.is_not_none(error_msg, "Alice không nhận được phản hồi lỗi")
        if error_msg:
            check.equal(
                error_msg.get('msg'), 
                'someone already in a match',
                "Nội dung lỗi không đúng"
            )
        print("CLIENT [Alice]: Nhận được lỗi 'someone already in a match' (SUCCESS).")
        
    finally:
        await alice.close()
        await bob.close()
        await charlie.close()


@pytest.mark.asyncio
async def test_challenge_and_game_win(server_process, check): # <-- Thêm check
    """
    Test case: Thách đấu, chấp nhận, và chơi đến khi thắng.
    """
    alice = TestClient("Alice_Win") # X
    bob = TestClient("Bob_Lose")    # O
    
    try:
        check.is_true(await alice.connect(), "Alice_Win kết nối thất bại")
        check.is_true(await bob.connect(), "Bob_Lose kết nối thất bại")

        # Luồng thách đấu
        print("CLIENT [Alice_Win]: Thách đấu Bob_Lose...")
        await alice.send({'type': 'challenge', 'opponent': 'Bob_Lose'})
        
        invite_msg = await bob.async_recv_msg_by_type('invite')
        check.is_not_none(invite_msg, "Bob_Lose không nhận được mời")
        if invite_msg:
            check.equal(invite_msg.get('from'), 'Alice_Win', "Lời mời từ sai người")
        print("CLIENT [Bob_Lose]: Nhận được lời mời từ Alice_Win.")

        await bob.send({'type': 'accept', 'opponent': 'Alice_Win'})
        
        # Kiểm tra bắt đầu trận đấu
        alice_start = await alice.async_recv_msg_by_type('match_start')
        bob_start = await bob.async_recv_msg_by_type('match_start')
        
        check.is_not_none(alice_start, "Alice_Win không nhận được 'match_start'")
        check.is_not_none(bob_start, "Bob_Lose không nhận được 'match_start'")
        
        if alice_start:
            check.equal(alice_start.get('you'), 'X', "Alice_Win không phải là X")
        if bob_start:
            check.equal(bob_start.get('you'), 'O', "Bob_Lose không phải là O")
        print("CLIENT [Alice_Win, Bob_Lose]: Trận đấu bắt đầu.")

        # Bắt đầu chơi (Alice X thắng)
        moves = [
            (alice, 5, 5), (bob, 0, 0),
            (alice, 6, 6), (bob, 1, 1),
            (alice, 7, 7), (bob, 2, 2),
            (alice, 8, 8), (bob, 3, 3),
            (alice, 9, 9)  # Nước đi chiến thắng
        ]
        
        current_player = alice
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
                opponent = bob if player is alice else alice
                check.is_not_none(
                    await opponent.async_recv_msg_by_type('opponent_move'),
                    f"{opponent.name} không nhận được 'opponent_move' ở lượt {i+1}"
                )
        
        print("CLIENT [Alice_Win]: Đánh nước đi chiến thắng...")
        
        # Kiểm tra kết quả
        alice_hl = await alice.async_recv_msg_by_type('highlight')
        bob_hl = await bob.async_recv_msg_by_type('highlight')
        check.is_not_none(alice_hl, "Alice_Win không nhận được highlight")
        check.is_not_none(bob_hl, "Bob_Lose không nhận được highlight")

        alice_end = await alice.async_recv_msg_by_type('match_end', timeout=7)
        bob_end = await bob.async_recv_msg_by_type('match_end', timeout=7)
        
        check.is_not_none(alice_end, "Alice_Win không nhận được 'match_end'")
        check.is_not_none(bob_end, "Bob_Lose không nhận được 'match_end'")
        
        if alice_end:
            check.equal(alice_end.get('result'), 'win', "Alice_Win không nhận 'win'")
        if bob_end:
            check.equal(bob_end.get('result'), 'lose', "Bob_Lose không nhận 'lose'")
        
        print("CLIENT [Alice_Win]: Nhận 'win' (SUCCESS).")
        print("CLIENT [Bob_Lose]: Nhận 'lose' (SUCCESS).")

    finally:
        await alice.close()
        await bob.close()


@pytest.mark.asyncio
async def test_game_timeout(server_process, check): # <-- Thêm check
    """
    Test case: Thua do hết giờ.
    Sử dụng 'pytest-check' để không dừng khi gặp lỗi đầu tiên.
    """
    TIMEOUT_VAL = THINK_TIME_SECONDS if 'THINK_TIME_SECONDS' in globals() else 15

    alice = TestClient("Alice_Timeout") # X
    bob = TestClient("Bob_Win_Timeout") # O
    
    try:
        check.is_true(await alice.connect(), "Alice kết nối thất bại")
        check.is_true(await bob.connect(), "Bob kết nối thất bại")

        # Vào trận nhanh
        await alice.send({'type': 'challenge', 'opponent': 'Bob_Win_Timeout'})
        await bob.async_recv_msg_by_type('invite')
        await bob.send({'type': 'accept', 'opponent': 'Alice_Timeout'})
        await alice.async_recv_msg_by_type('match_start')
        await bob.async_recv_msg_by_type('match_start')
        
        # Đến lượt Alice
        check.is_not_none(
            await alice.async_recv_msg_by_type('your_turn'),
            "Alice không nhận được 'your_turn'"
        )
        print(f"CLIENT [Alice_Timeout]: Đến lượt. Bắt đầu chờ {TIMEOUT_VAL + 2} giây...")
        
        # Không làm gì cả, chờ server xử timeout
        await asyncio.sleep(TIMEOUT_VAL + 2)
        
        print("CLIENT: Hết thời gian chờ. Kiểm tra kết quả timeout...")
        
        # --- PHẦN KIỂM TRA ĐÃ SỬA ---
        
        # 1. Kiểm tra Alice (người thua)
        alice_end = await alice.async_recv_msg_by_type('match_end', timeout=5)
        check.is_not_none(
            alice_end, 
            "Alice KHÔNG nhận được tin nhắn 'match_end' (bị timeout 5s)"
        )
        
        # 2. Kiểm tra Bob (người thắng)
        bob_end = await bob.async_recv_msg_by_type('match_end', timeout=5)
        check.is_not_none(
            bob_end, 
            "Bob KHÔNG nhận được tin nhắn 'match_end' (bị timeout 5s)"
        )

        # 3. Phân tích kết quả (nếu có)
        if alice_end:
            check.equal(alice_end.get('reason'), 'timeout', "Lý do của Alice không phải 'timeout'")
            check.equal(alice_end.get('winner'), 'opponent', "Kết quả của Alice không phải 'opponent'")
        else:
            check.fail("Không thể kiểm tra reason/winner của Alice vì alice_end là None")

        if bob_end:
            check.equal(bob_end.get('reason'), 'timeout', "Lý do của Bob không phải 'timeout'")
            check.equal(bob_end.get('winner'), 'you', "Kết quả của Bob không phải 'you'")
        else:
            check.fail("Không thể kiểm tra reason/winner của Bob vì bob_end là None")

    finally:
        await alice.close()
        await bob.close()