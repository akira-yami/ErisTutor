# -*- coding: utf-8 -*-
from __future__ import annotations

"""Backend Chromium IAccessible2 (IA2) cho Windows.

Mục tiêu của module này không phải biến accessibility tree thành DOM. Nó chỉ
đọc các thuộc tính accessibility cần cho semantic desktop: role, name, state,
bounds và quan hệ con. Đường ưu tiên bám đúng thiết kế Deep Environment:

    AccessibleObjectFromWindow -> IAccessible -> IServiceProvider
    -> QueryService(IID_IAccessible2)

Nếu máy không có ``comtypes`` hoặc provider không expose IA2, caller phải rơi
về UI Automation. Không có bất kỳ đường đọc cookie/network/JavaScript nào ở đây.
"""

import ctypes
import os
from dataclasses import dataclass
from typing import Any


# IID chính thức của IAccessible2. Chỉ dùng để QueryService xác nhận/nhận IA2.
IID_IAccessible2 = "E89F726E-C4F4-4C19-BB19-B647D7FA8478"
OBJID_CLIENT = 0xFFFFFFFC
STATE_SYSTEM_SELECTED = 0x00000002
STATE_SYSTEM_FOCUSED = 0x00000004
STATE_SYSTEM_UNAVAILABLE = 0x00000001


@dataclass(frozen=True, slots=True)
class IA2NodeSnapshot:
    runtime_id: str
    role: str
    name: str = ""
    value: str = ""
    bounds: tuple[int, int, int, int] | None = None
    selected: bool = False
    has_focus: bool = False
    enabled: bool = True
    depth: int = 0


class IA2AccessibilityBackend:
    """Đọc cây MSAA/IA2 theo HWND với giới hạn node/depth rõ ràng.

    ``comtypes`` được giữ là dependency Windows tùy chọn để Linux CI vẫn import
    source bình thường. Trên Windows thật, ``available`` chỉ True khi toàn bộ
    typelib/IID cần thiết nạp thành công.
    """

    def __init__(self, *, max_nodes: int = 260, max_depth: int = 8) -> None:
        self.max_nodes = max(20, int(max_nodes))
        self.max_depth = max(1, int(max_depth))
        self._ready = False
        self._error = ""
        self._comtypes: Any = None
        self._IAccessible: Any = None
        self._IServiceProvider: Any = None
        self._GUID: Any = None
        self._oleacc = None
        if os.name == "nt":
            self._initialize()

    @property
    def available(self) -> bool:
        return bool(self._ready)

    @property
    def unavailable_reason(self) -> str:
        return self._error

    def _initialize(self) -> None:  # pragma: no cover - chỉ chạy Windows E2E
        try:
            import comtypes  # type: ignore
            from comtypes import COMMETHOD, GUID, HRESULT, IUnknown  # type: ignore
            from ctypes import POINTER, c_void_p

            # oleacc.dll có type library của MSAA/IAccessible. GetModule chỉ tạo
            # wrapper Python, không khởi chạy browser hay đụng nội dung trang.
            from comtypes.client import GetModule  # type: ignore
            GetModule("oleacc.dll")
            try:
                from comtypes.gen.Accessibility import IAccessible  # type: ignore
            except Exception:
                # Tên package sinh ra có thể khác theo bản comtypes/Windows.
                from comtypes.gen.stdole import IDispatch  # type: ignore
                IAccessible = IDispatch

            class IServiceProvider(IUnknown):
                _iid_ = GUID("{6D5140C1-7436-11CE-8034-00AA006009FA}")
                _methods_ = [
                    COMMETHOD(
                        [], HRESULT, "QueryService",
                        (['in'], POINTER(GUID), 'guidService'),
                        (['in'], POINTER(GUID), 'riid'),
                        (['out'], POINTER(c_void_p), 'ppvObject'),
                    )
                ]

            self._comtypes = comtypes
            self._IAccessible = IAccessible
            self._IServiceProvider = IServiceProvider
            self._GUID = GUID
            self._oleacc = ctypes.OleDLL("oleacc")
            self._ready = True
        except Exception as exc:
            self._error = f"IA2 init failed: {type(exc).__name__}: {exc}"
            self._ready = False

    def _root_accessible(self, hwnd: int) -> tuple[Any, bool]:  # pragma: no cover - Windows E2E
        """Trả IAccessible root và cờ provider có IA2 hay không."""
        if not self.available:
            return None, False
        from ctypes import POINTER, byref, c_void_p

        iid_acc = self._IAccessible._iid_
        raw = c_void_p()
        hr = int(self._oleacc.AccessibleObjectFromWindow(
            ctypes.c_void_p(int(hwnd)), ctypes.c_uint(OBJID_CLIENT), byref(iid_acc), byref(raw)
        ))
        if hr < 0 or not raw.value:
            return None, False
        root = ctypes.cast(raw, POINTER(self._IAccessible))

        ia2_ok = False
        try:
            service = root.QueryInterface(self._IServiceProvider)
            guid = self._GUID("{" + IID_IAccessible2 + "}")
            ia2_raw = service.QueryService(byref(guid), byref(guid))
            ia2_value = int(ia2_raw or 0)
            ia2_ok = bool(ia2_value)
            if ia2_value:
                # QueryService AddRef; release ngay vì semantic hiện chỉ cần
                # xác nhận IA2 provider rồi đọc contract chung IAccessible.
                unk = ctypes.cast(c_void_p(ia2_value), POINTER(self._comtypes.IUnknown))
                try:
                    unk.Release()
                except Exception:
                    pass
        except Exception:
            ia2_ok = False
        return root, ia2_ok

    @staticmethod
    def _variant_child_self() -> Any:  # pragma: no cover - Windows E2E
        try:
            from comtypes.automation import VARIANT  # type: ignore
            child = VARIANT()
            child.value = 0
            return child
        except Exception:
            return 0

    @staticmethod
    def _safe_call(obj: Any, name: str, child: Any, default: Any = "") -> Any:  # pragma: no cover
        try:
            member = getattr(obj, name)
            return member(child) if callable(member) else member
        except Exception:
            return default

    def _role_text(self, role: Any) -> str:  # pragma: no cover - Windows E2E
        try:
            role_id = int(role)
        except Exception:
            return str(role or "accessible")
        try:
            buf = ctypes.create_unicode_buffer(128)
            n = int(self._oleacc.GetRoleTextW(role_id, buf, len(buf)))
            if n > 0:
                return buf.value
        except Exception:
            pass
        return f"role_{role_id}"

    def _snapshot_one(self, obj: Any, *, depth: int, ordinal: int) -> IA2NodeSnapshot | None:  # pragma: no cover
        child_self = self._variant_child_self()
        try:
            name = str(self._safe_call(obj, "accName", child_self, "") or "").strip()
            value = str(self._safe_call(obj, "accValue", child_self, "") or "").strip()
            role = self._role_text(self._safe_call(obj, "accRole", child_self, 0))
            state_raw = self._safe_call(obj, "accState", child_self, 0)
            try:
                state = int(state_raw or 0)
            except Exception:
                state = 0

            bounds = None
            try:
                left = ctypes.c_long(); top = ctypes.c_long(); width = ctypes.c_long(); height = ctypes.c_long()
                obj.accLocation(ctypes.byref(left), ctypes.byref(top), ctypes.byref(width), ctypes.byref(height), child_self)
                if width.value > 0 and height.value > 0:
                    bounds = (int(left.value), int(top.value), int(width.value), int(height.value))
            except Exception:
                pass

            # Runtime id không được làm từ raw text dài. HWND/provider + ordinal
            # được caller ghép thêm khi cần; ở đây chỉ cần ID ổn định trong snapshot.
            runtime_id = f"ia2:{depth}:{ordinal}:{role}:{name[:80]}"
            return IA2NodeSnapshot(
                runtime_id=runtime_id,
                role=role,
                name=name,
                value=value,
                bounds=bounds,
                selected=bool(state & STATE_SYSTEM_SELECTED),
                has_focus=bool(state & STATE_SYSTEM_FOCUSED),
                enabled=not bool(state & STATE_SYSTEM_UNAVAILABLE),
                depth=depth,
            )
        except Exception:
            return None

    def _children(self, obj: Any) -> list[Any]:  # pragma: no cover - Windows E2E
        """Lấy child object. Child-ID đơn lẻ được bỏ qua để tránh dereference mù."""
        try:
            count_raw = obj.accChildCount
            count = int(count_raw() if callable(count_raw) else count_raw)
        except Exception:
            return []
        if count <= 0:
            return []
        count = min(count, self.max_nodes)
        try:
            from comtypes.automation import VARIANT  # type: ignore
            array = (VARIANT * count)()
            obtained = ctypes.c_long()
            hr = int(self._oleacc.AccessibleChildren(obj, 0, count, array, ctypes.byref(obtained)))
            if hr < 0:
                return []
            children: list[Any] = []
            for index in range(int(obtained.value)):
                value = array[index].value
                # Khi VARIANT là IDispatch/com object thì có QueryInterface.
                if hasattr(value, "QueryInterface"):
                    try:
                        children.append(value.QueryInterface(self._IAccessible))
                    except Exception:
                        children.append(value)
            return children
        except Exception:
            return []

    def snapshot(self, hwnd: int) -> tuple[tuple[IA2NodeSnapshot, ...], bool]:
        """Đọc bounded tree. Tuple thứ hai cho biết IA2 QueryService thành công."""
        if not self.available or not hwnd:
            return (), False
        try:  # pragma: no cover - Windows E2E
            self._comtypes.CoInitializeEx(self._comtypes.COINIT_MULTITHREADED)
        except Exception:
            try:
                self._comtypes.CoInitialize()
            except Exception:
                pass
        try:  # pragma: no cover - Windows E2E
            root, ia2_ok = self._root_accessible(int(hwnd))
            if root is None:
                return (), False
            nodes: list[IA2NodeSnapshot] = []
            stack: list[tuple[Any, int]] = [(root, 0)]
            ordinal = 0
            while stack and len(nodes) < self.max_nodes:
                obj, depth = stack.pop()
                ordinal += 1
                node = self._snapshot_one(obj, depth=depth, ordinal=ordinal)
                if node is not None:
                    nodes.append(node)
                if depth >= self.max_depth:
                    continue
                children = self._children(obj)
                for child in reversed(children):
                    if len(stack) + len(nodes) >= self.max_nodes:
                        break
                    stack.append((child, depth + 1))
            return tuple(nodes), ia2_ok
        except Exception:
            return (), False
        finally:  # pragma: no cover
            try:
                self._comtypes.CoUninitialize()
            except Exception:
                pass


__all__ = ["IA2AccessibilityBackend", "IA2NodeSnapshot", "IID_IAccessible2"]
