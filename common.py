from __future__ import annotations
import asyncio
import json
import codecs
from typing import Any, Dict, Tuple, List, Optional

# ==========================
# HẰNG SỐ CƠ BẢN CHO TRÒ CHƠI
# ==========================
BOARD_SIZE = 15                         # Kích thước bàn cờ 15x15
THINK_TIME_SECONDS = 30                 # Giới hạn thời gian cho mỗi lượt (30 giây)
WIN_LENGTH = 5                          # Số quân liên tiếp cần để thắng
COORDS = "ABCDEFGHIJKLMNO"              # Ký hiệu các cột A → O (15 cột)
DIRS = [(1, 0), (0, 1), (1, 1), (1, -1)]  # 4 hướng kiểm tra: ngang, dọc, chéo chính, chéo phụ

# ==========================
# GỬI VÀ NHẬN DỮ LIỆU JSON QUA SOCKET
# ==========================

# Cache UTF-8 decoder để tăng hiệu suất
_decoder = codecs.getincrementaldecoder('utf-8')()

async def send_json(writer: asyncio.StreamWriter, obj: Dict[str, Any]) -> None:
    """
    Gửi một object Python (dict) sang client/server qua kết nối TCP.
    Dữ liệu được chuyển sang JSON + thêm ký tự xuống dòng '\n' để tách gói tin.
    
    Args:
        writer: StreamWriter để gửi dữ liệu
        obj: Dictionary chứa dữ liệu cần gửi
    """
    if writer.is_closing():
        raise ConnectionError("Writer đã đóng")
    
    try:
        data = json.dumps(obj, ensure_ascii=False) + '\n'
        writer.write(data.encode('utf-8'))
        await writer.drain()
    except Exception as e:
        print(f"[ERROR] send_json failed: {e}")
        raise


async def recv_json(reader: asyncio.StreamReader) -> Dict[str, Any]:
    """
    Đọc 1 dòng dữ liệu JSON từ socket (do bên kia gửi bằng send_json()).
    
    Args:
        reader: StreamReader để đọc dữ liệu
        
    Returns:
        Dictionary chứa dữ liệu đã parse
        
    Raises:
        ConnectionError: Nếu kết nối bị đóng
        json.JSONDecodeError: Nếu dữ liệu không phải JSON hợp lệ
    """
    line = await reader.readline()
    if not line:
        raise ConnectionError("Kết nối đã bị đóng")
    
    # Sử dụng cached decoder để tăng hiệu suất
    text = _decoder.decode(line, final=False).strip()
    return json.loads(text)


# ==========================
# XỬ LÝ TỌA ĐỘ
# ==========================

def parse_coord(token: str) -> Optional[Tuple[int, int]]:
    """
    Phân tích chuỗi tọa độ do người chơi nhập và chuyển thành cặp (x, y).
    
    Args:
        token: Chuỗi tọa độ nhập vào
        
    Returns:
        (x, y): Tuple tọa độ (0-based index) nếu hợp lệ
        None: Nếu chuỗi không hợp lệ
    """
    if not token:
        return None
    
    token = token.strip().lower()
    
    try:
        parts = token.split(',')
        if len(parts) == 2:
            x, y = int(parts[0].strip()), int(parts[1].strip())
            if 0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE:
                return x, y
    except (ValueError, IndexError):
        pass
    return None


def format_coord(x: int, y: int) -> str:
    """
    Chuyển tọa độ số (x, y) thành định dạng chữ-số dễ đọc (ví dụ: A5, C10).
    
    Args:
        x: Tọa độ cột (0-based)
        y: Tọa độ hàng (0-based)
        
    Returns:
        Chuỗi tọa độ dạng "A5", "C10"
    """
    if 0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE:
        return f"{COORDS[x]}{y + 1}"
    return f"({x},{y})"


# ==========================
# KIỂM TRA THẮNG/THUA
# ==========================

def check_win(board: List[List[str]], x: int, y: int, symbol: str) -> bool:
    """
    Kiểm tra xem người chơi có thắng không sau khi đánh tại (x, y).
    
    Args:
        board: Ma trận bàn cờ
        x: Tọa độ cột vừa đánh
        y: Tọa độ hàng vừa đánh
        symbol: Ký hiệu của người chơi ('X' hoặc 'O')
        
    Returns:
        True nếu có ít nhất WIN_LENGTH quân liên tiếp
        False nếu chưa thắng
    """
    n = len(board)
    
    # Duyệt qua 4 hướng: ngang, dọc, chéo chính, chéo phụ
    for dx, dy in DIRS:
        count = 1  # Đếm ô hiện tại
        
        # Kiểm tra theo cả hai chiều của hướng này
        for direction in (1, -1):
            nx, ny = x, y
            
            while True:
                nx += dx * direction
                ny += dy * direction
                
                # Kiểm tra biên và ký hiệu
                if (0 <= nx < n and 0 <= ny < n and 
                    board[ny][nx] == symbol):
                    count += 1
                else:
                    break
        
        # Nếu đủ số quân liên tiếp → thắng
        if count >= WIN_LENGTH:
            return True
    
    return False


def find_win_line(board: List[List[str]], x: int, y: int, symbol: str) -> List[Tuple[int, int]]:
    """
    Tìm danh sách các ô tạo thành đường thắng (>=WIN_LENGTH quân liên tiếp)
    có chứa vị trí (x, y) của người chơi.
    
    Args:
        board: Ma trận bàn cờ
        x: Tọa độ cột vừa đánh
        y: Tọa độ hàng vừa đánh
        symbol: Ký hiệu của người chơi ('X' hoặc 'O')
        
    Returns:
        Danh sách tọa độ [(x1, y1), (x2, y2), ...] của đường thắng
        Danh sách rỗng [] nếu không có đường thắng
    """
    n = len(board)
    best_line: List[Tuple[int, int]] = []
    
    # Duyệt qua 4 hướng: ngang, dọc, chéo chính, chéo phụ
    for dx, dy in DIRS:
        current_line = [(x, y)]
        
        # Kiểm tra cả hai chiều của hướng này
        for direction in (1, -1):
            nx, ny = x, y
            
            while True:
                nx += dx * direction
                ny += dy * direction
                
                # Kiểm tra biên và ký hiệu
                if (0 <= nx < n and 0 <= ny < n and 
                    board[ny][nx] == symbol):
                    # Thêm vào đầu nếu đi ngược, vào cuối nếu đi xuôi
                    if direction == -1:
                        current_line.insert(0, (nx, ny))
                    else:
                        current_line.append((nx, ny))
                else:
                    break
        
        # Cập nhật đường dài nhất
        if len(current_line) > len(best_line):
            best_line = current_line
    
    # Chỉ trả về nếu đủ số quân thắng
    return best_line if len(best_line) >= WIN_LENGTH else []


def is_board_full(board: List[List[str]]) -> bool:
    """
    Kiểm tra xem bàn cờ đã đầy chưa (hòa).
    
    Args:
        board: Ma trận bàn cờ
        
    Returns:
        True nếu không còn ô trống
        False nếu còn ô trống
    """
    for row in board:
        if '.' in row:
            return False
    return True


def count_moves(board: List[List[str]]) -> int:
    """
    Đếm số nước đã đánh trên bàn cờ.
    
    Args:
        board: Ma trận bàn cờ
        
    Returns:
        Số lượng ô đã có quân (X hoặc O)
    """
    count = 0
    for row in board:
        for cell in row:
            if cell != '.':
                count += 1
    return count


# ==========================
# VALIDATION
# ==========================

def validate_move(board: List[List[str]], x: int, y: int) -> tuple[bool, str]:
    """
    Kiểm tra tính hợp lệ của một nước đi.
    
    Args:
        board: Ma trận bàn cờ
        x: Tọa độ cột
        y: Tọa độ hàng
        
    Returns:
        (True, "OK") nếu hợp lệ
        (False, "lý do lỗi") nếu không hợp lệ
    """
    n = len(board)
    
    # Kiểm tra tọa độ trong phạm vi
    if not (0 <= x < n and 0 <= y < n):
        return False, f"Tọa độ ngoài phạm vi (0-{n-1})"
    
    # Kiểm tra ô có trống không
    if board[y][x] != '.':
        return False, "Ô này đã có quân"
    
    return True, "OK"


# ==========================
# TIỆN ÍCH HIỂN THỊ
# ==========================

def print_board(board: List[List[str]]) -> None:
    """
    In bàn cờ ra console (dùng cho CLI client).
    
    Args:
        board: Ma trận bàn cờ
    """
    n = len(board)
    
    # In header cột (A B C D ...)
    print("   " + " ".join(COORDS[:n]))
    print("  +" + "-" * (n * 2 - 1) + "+")
    
    # In từng hàng
    for i, row in enumerate(board):
        # In số hàng và nội dung
        row_str = " ".join(cell if cell != '.' else '·' for cell in row)
        print(f"{i+1:2d}|{row_str}|")
    
    print("  +" + "-" * (n * 2 - 1) + "+")


def board_to_string(board: List[List[str]]) -> str:
    """
    Chuyển bàn cờ thành chuỗi để lưu hoặc truyền đi.
    
    Args:
        board: Ma trận bàn cờ
        
    Returns:
        Chuỗi đại diện cho bàn cờ
    """
    return '\n'.join(''.join(row) for row in board)


def string_to_board(s: str, size: int = BOARD_SIZE) -> List[List[str]]:
    """
    Chuyển chuỗi thành ma trận bàn cờ.
    
    Args:
        s: Chuỗi đại diện bàn cờ
        size: Kích thước bàn cờ
        
    Returns:
        Ma trận bàn cờ
    """
    lines = s.strip().split('\n')
    board = []
    for line in lines[:size]:
        row = list(line[:size])
        # Đảm bảo đủ kích thước
        while len(row) < size:
            row.append('.')
        board.append(row)
    
    # Đảm bảo đủ số hàng
    while len(board) < size:
        board.append(['.'] * size)
    
    return board
