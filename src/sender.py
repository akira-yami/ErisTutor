import socket  # Socket dùng để tạo kết nối TCP/IP
import sys
import time  # Thư viện time để sử dụng hàm sleep

# Console Windows mặc định là cp1252 -> in tiếng Việt có dấu sẽ ném
# UnicodeEncodeError. Ép stdout/stderr sang UTF-8 để script chạy được ở mọi shell.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

# Cấu hình địa chỉ và cổng
UDP_IP = "127.0.0.1"  # Localhost (chính máy của bạn)
UDP_PORT = 4242
MESSAGE = "Hello World"


def main() -> None:
    # Khởi tạo Socket UDP
    # AF_INET: Sử dụng IPv4
    # SOCK_DGRAM: Giao thức UDP
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(f"Đang gửi tin nhắn đến {UDP_IP}:{UDP_PORT}...")

    try:
        while True:
            # Gửi tin nhắn (Phải encode sang bytes trước khi gửi)
            sock.sendto(MESSAGE.encode("utf-8"), (UDP_IP, UDP_PORT))
            print(f"Đã gửi: {MESSAGE}")
            time.sleep(1)  # Đợi 1 giây trước khi gửi tin nhắn tiếp theo
    except KeyboardInterrupt:
        print("Đã dừng gửi tin nhắn.")
    finally:
        sock.close()  # Đóng socket khi hoàn thành


# Chỉ chạy vòng lặp gửi khi chạy trực tiếp; import module không được khởi động loop.
if __name__ == "__main__":
    main()
