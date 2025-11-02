from __future__ import annotations
import asyncio, json
from typing import Any, Dict, Tuple, List

# ==========================
# HẰNG SỐ CƠ BẢN CHO TRÒ CHƠI
# ==========================
BOARD_SIZE = 15                           # Kích thước bàn cờ 15x15
THINK_TIME_SECONDS = 15                 # Giới hạn thời gian cho mỗi lượt (15 giây)
COORDS = "ABCDEFGHIJKLMNO"  # 15 cột    # Ký hiệu các cột A → O (15 cột)
DIRS = [(1,0), (0,1), (1,1), (1,-1)]    # 4 hướng kiểm tra thắng: ngang, dọc, chéo chính, chéo phụ

# ==========================
# GỬI DỮ LIỆU DẠNG JSON QUA SOCKET
# ==========================
async def send_json(writer: asyncio.StreamWriter, obj: Dict[str, Any]):
    """
    Gửi một object Python (dict) sang client/server qua kết nối TCP.
    Dữ liệu được chuyển sang JSON + thêm ký tự xuống dòng '\n' để tách gói tin.
    """
    data = json.dumps(obj, ensure_ascii=False) + '\n'
    writer.write(data.encode('utf-8'))  # Gửi dữ liệu ra socket
    await writer.drain()                # Đảm bảo dữ liệu đã được gửi đi

# ==========================
# NHẬN DỮ LIỆU DẠNG JSON QUA SOCKET
# ==========================

async def recv_json(reader: asyncio.StreamReader) -> Dict[str, any]:
    """
    Đọc 1 dòng dữ liệu JSON từ socket (do bên kia gửi bằng send_json()).
    Nếu kết nối bị đóng, raise lỗi ConnectionError.
    """
    line = await reader.readline()  # Đọc một dòng dữ liệu từ socket
    if not line:
        raise ConnectionError("Kết nối đã bị đóng")
    return json.loads(line.decode('utf-8').strip())  # Giải mã JSON và trả về object Python

def parse_coord(token: str) -> Tuple[int, int] | None:
    """
    Hàm phân tích chuỗi tọa độ do người chơi nhập (ví dụ: "A5", "3,4", "3 4")
    và chuyển thành cặp tọa độ dạng số (x, y) tương ứng với chỉ số trên bàn cờ.
    
    Trả về:
        (x, y): nếu tọa độ hợp lệ (0-based index)
        None : nếu chuỗi nhập sai định dạng
    """
    token = token.strip().lower()       # Loại bỏ khoảng trắng đầu/cuối và chuyển về chữ thường để xử lý dễ hơn
    # ================================
    # Trường hợp 1: dạng "x,y"
    # ================================
    if ',' in token:
        try:# Tách chuỗi theo dấu phẩy, ví dụ "3,4" → ["3", "4"]
            x, y = token.split(',')         # Tách chuỗi theo dấu phẩy, ví dụ "3,4" → ["3", "4"]
            return int(x), int(y)           # Chuyển sang số nguyên và trả về tuple (x, y)
        except: return None                 # Nếu lỗi (ví dụ nhập "3,a"), trả về None
    # ================================
    # Trường hợp 2: dạng "x y" (cách nhau bằng khoảng trắng)
    # ================================
    if ' ' in token:
        try:
            x, y = token.split()            # Tách chuỗi theo khoảng trắng, ví dụ "3 4" → ["3", "4"]
            return int(x), int(y)
        except: return None
    # ================================
    # Trường hợp 3: dạng "A5", "c10", v.v.
    # ================================
    if token and token[0].isalpha():        # Ký tự đầu tiên là chữ cái (cột)
        col = token[0].upper()              # Chuyển sang chữ hoa để tra trong COORDS      
        if col in COORDS:                   # Kiểm tra cột có hợp lệ không    
            try:
                row = int(token[1:])        # Lấy phần số sau chữ (hàng), ví dụ "A5" → row = 5
                # Tìm chỉ số cột trong chuỗi COORDS (A=0, B=1, ..., O=14)
                # và trừ 1 cho hàng vì index trong mảng bắt đầu từ 0
                return COORDS.index(col), row - 1
            except: return None
    # ================================
    # Nếu không khớp bất kỳ định dạng nào ở trên → trả về None
    # ================================
    return None

def check_win(board: List[List[str]], x: int, y: int, symbol: str) -> bool:
    """
    Kiểm tra xem người chơi (ký hiệu 'symbol') có thắng không
    sau khi vừa đánh tại vị trí (x, y) trên bàn cờ.

    Trả về:
        True  → nếu có ít nhất 5 quân liên tiếp cùng ký hiệu
        False → nếu chưa đủ điều kiện thắng
    """
    n = len(board)           # # Lấy kích thước bàn cờ (thường là 15 trong cờ caro)
    # Duyệt qua 4 hướng cần kiểm tra: ngang, dọc, chéo chính, chéo phụ
    for dx, dy in DIRS:
        cnt = 1             # # Đếm số quân liên tiếp cùng ký hiệu (tính cả ô hiện tại)
        # Kiểm tra theo 2 chiều của hướng hiện tại (xuôi và ngược)
        for s in (1, -1):
            nx, ny = x, y   # Bắt đầu từ vị trí vừa đánh
            # Duyệt liên tục theo hướng (dx, dy) cho đến khi ra ngoài hoặc gặp quân khác
            while True:
                nx += dx * s        # Di chuyển 1 ô theo hướng X
                ny += dy * s        # Di chuyển 1 ô theo hướng Y
                # Kiểm tra xem ô mới có nằm trong bàn cờ và cùng ký hiệu không
                if 0 <= nx < n and 0 <= ny < n and board[ny][nx] == symbol:
                    cnt += 1        # Nếu cùng ký hiệu → cộng thêm 1 quân liên tiếp
                else:
                    break           # Nếu khác hoặc ra ngoài bàn → dừng kiểm tra hướng này
        # Nếu trong 1 hướng có tổng cộng >= 5 quân liên tiếp → thắng
        if cnt >= 5:
            return True
    # Nếu duyệt hết 4 hướng mà không đủ 5 quân liên tiếp → chưa thắng
    return False

def find_win_line(board: List[List[str]], x: int, y: int, symbol: str) -> List[Tuple[int, int]]:
    """
    Tìm danh sách các ô tạo thành một đường thắng (>=5 quân liên tiếp)
    có chứa vị trí (x, y) của người chơi hiện tại (symbol).

    Trả về:
        - Danh sách tọa độ [(x1, y1), (x2, y2), ...] nếu tìm thấy đường thắng
        - Danh sách rỗng [] nếu không có đường nào đủ 5 quân
    """
    n = len(board)      # Lấy kích thước bàn cờ (thường là 15x15)
    best: List[Tuple[int, int]] = []    # Biến lưu lại đường thắng dài nhất tìm được
    # Duyệt qua 4 hướng: ngang, dọc, chéo chính, chéo phụ
    for dx, dy in DIRS:
        # Danh sách tạm lưu các ô liên tiếp có cùng ký hiệu trong hướng hiện tại
        cells = [(x, y)]        # Bắt đầu từ ô vừa đánh
        # Kiểm tra cả hai chiều của hướng đó (xuôi và ngược)
        for s in (1, -1):
            nx, ny = x, y       # Bắt đầu từ vị trí vừa đánh
            # Duyệt liên tục theo hướng (dx, dy) cho đến khi ra ngoài hoặc gặp quân khác
            while True:
                nx += dx * s    # Di chuyển theo trục X
                ny += dy * s    # Di chuyển theo trục Y
                # Kiểm tra còn nằm trong bàn cờ và có cùng ký hiệu không
                if 0 <= nx < n and 0 <= ny < n and board[ny][nx] == symbol:
                    # Nếu đi xuôi (s == 1) → thêm vào cuối danh sách
                    if s == 1:
                        cells.append((nx, ny))
                    # Nếu đi ngược (s == -1) → thêm vào đầu danh sách
                    else:
                        # Gặp biên hoặc ô khác ký hiệu → dừng kiểm tra hướng này
                        cells.insert(0, (nx, ny))
                else:
                    break
        # Sau khi kiểm tra cả hai chiều, nếu đường này dài hơn đường cũ → cập nhật "best"
        if len(cells) > len(best):
            best = cells
    # Nếu đường thắng có ít nhất 5 quân → trả về danh sách đó
    # Ngược lại → không có đường thắng → trả về danh sách rỗng
    return best if len(best) >= 5 else []