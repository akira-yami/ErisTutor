from .decision import Decision
#nhập tương đối (relative import).
__all__ = ["Decision"]
# Việc sử dụng __all__ giúp kiểm soát những gì được xuất ra khi sử dụng from domain import *,
# đảm bảo rằng chỉ có lớp Decision được truy cập từ bên ngoài module domain,
# trong khi các thành phần khác có thể được giữ lại cho nội bộ module nếu cần thiết
