# -*- coding: utf-8 -*-
"""Renderer Live2D được body Godot bật/tắt theo context menu.

Godot sở hữu mode switch và forward event ``eris.stage`` sang UDP 4252. Renderer
vẽ model, phối affect/emotion/idle/speech face và gửi yêu cầu context menu về UDP 4242.
"""

from .affect import AffectPose, AffectVector
from .stage_state import Live2DStageState, Live2DSnapshot

__all__ = ["AffectPose", "AffectVector", "Live2DStageState", "Live2DSnapshot"]
