from __future__ import annotations
import asyncio, json
from typing import Any, Dict, Tuple, List

# ==========================
# HẰNG SỐ CƠ BẢN CHO TRÒ CHƠI
# ==========================
BOARD_SIZE = 15                         # Kích thước bàn cờ 15x15
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