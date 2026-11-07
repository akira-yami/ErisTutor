# -*- coding: utf-8 -*-
"""Các subsystem nhận thức chuyên biệt còn được runtime sử dụng.

Package root cố ý không re-export hàng chục engine. Import một tiện ích không nên
khởi tạo cả thought stream, internal state và social presence chỉ vì Python thích
thực thi ``__init__`` trước khi làm việc tử tế.
"""

__all__: list[str] = []
