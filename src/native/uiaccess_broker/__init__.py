# -*- coding: utf-8 -*-
"""UIAccess broker hẹp cho Eris Deep Environment.

Broker này là optional. Core Eris vẫn chạy medium-integrity; chỉ executable
broker đã ký và cài ở vị trí được Windows bảo vệ mới dùng uiAccess=true.
"""

from .client import UIAccessBrokerClient
from .protocol import BrokerRequest, BrokerResponse

__all__ = ["BrokerRequest", "BrokerResponse", "UIAccessBrokerClient"]
