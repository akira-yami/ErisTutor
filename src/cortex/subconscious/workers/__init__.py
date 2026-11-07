# -*- coding: utf-8 -*-
"""Worker Phase 1/2/3/4/5/6/7 cho subconscious runtime."""

from .user_modeler import UserModelerAgent
from .memory_resonator import MemoryResonatorAgent
from .goal_simulator import GoalSimulatorAgent
from .self_regulator import SelfRegulatorAgent
from .rehearsal import RehearsalAgent
from .curiosity_gap import CuriosityGapAgent
from .daydream_injector import DaydreamInjector
from .independent_agenda import IndependentAgendaAgent
from .spatial_mind import SpatialMindAgent
from .subjective_time import SubjectiveTimeAgent
from .meta_audit import MetaCognitiveAuditAgent, MetaAuditFinding, MetaAuditResult
from .shadow_accumulator import ShadowAccumulator, ShadowAccumulatorPolicy
from .belief_mutation import (
    BeliefMutationEngine,
    BeliefMutationProposal,
    PreferenceDeltaProposal,
    HabitDeltaProposal,
    MutationCommitter,
    MutationCommitRecord,
    MutationDecision,
)

__all__ = [
    "UserModelerAgent",
    "MemoryResonatorAgent",
    "GoalSimulatorAgent",
    "SelfRegulatorAgent",
    "RehearsalAgent",
    "CuriosityGapAgent",
    "DaydreamInjector",
    "IndependentAgendaAgent",
    "SpatialMindAgent",
    "SubjectiveTimeAgent",
    "MetaCognitiveAuditAgent",
    "MetaAuditFinding",
    "MetaAuditResult",
    "ShadowAccumulator",
    "ShadowAccumulatorPolicy",
    "BeliefMutationEngine",
    "BeliefMutationProposal",
    "PreferenceDeltaProposal",
    "HabitDeltaProposal",
    "MutationCommitter",
    "MutationCommitRecord",
    "MutationDecision",
    "build_phase12_workers",
    "build_phase34_workers",
    "build_phase1234_workers",
    "build_phase5_workers",
    "build_phase7_workers",
    "build_phase6_workers",
    "build_phase1234567_workers",
    "build_phase123457_workers",
    "build_default_subconscious_workers",
]


def build_phase12_workers(*, allow_surface_memory_echo: bool = False):
    """Tạo worker set Phase 1+2 mặc định.

    Mặc định memory echo không surface trực tiếp. Integration sâu hơn có thể bật
    `allow_surface_memory_echo=True` sau khi guard/runtime đã kiểm soát tốt.
    """

    return (
        UserModelerAgent(),
        MemoryResonatorAgent(allow_surface_echo=allow_surface_memory_echo),
        GoalSimulatorAgent(),
        SelfRegulatorAgent(),
        CuriosityGapAgent(),
        RehearsalAgent(),
    )


def build_phase34_workers(
    *,
    allow_daydream_surface: bool = False,
    allow_agenda_later_surface: bool = False,
):
    """Tạo worker set Phase 3+4.

    Surface mặc định đều tắt. Phase 3/4 tạo vật liệu nội tâm và state pressure,
    không tự biến thành lời thoại. Muốn mở public surface thì phải bật có chủ ý,
    sau khi SurfaceArbiter/Permission đã có test. Tức là không đốt nhà để xem báo
    cháy có hoạt động không.
    """

    return (
        DaydreamInjector(allow_surface=allow_daydream_surface),
        IndependentAgendaAgent(allow_later_surface=allow_agenda_later_surface),
        SpatialMindAgent(),
        SubjectiveTimeAgent(),
    )


def build_phase1234_workers(
    *,
    allow_surface_memory_echo: bool = False,
    allow_daydream_surface: bool = False,
    allow_agenda_later_surface: bool = False,
):
    """Tạo bộ worker Phase 1 → 4 để đưa vào SubconsciousLoop."""

    return (
        *build_phase12_workers(allow_surface_memory_echo=allow_surface_memory_echo),
        *build_phase34_workers(
            allow_daydream_surface=allow_daydream_surface,
            allow_agenda_later_surface=allow_agenda_later_surface,
        ),
    )


def build_phase5_workers():
    """Tạo worker Phase 5 Meta-Cognitive Audit.

    MetaAudit đặt sau Phase 1-4 để dọn Blackboard trước khi synthesis/public seed.
    Nó không phải guard văn phong; nó là hệ miễn dịch chống loop/hallucinated link.
    """

    return (MetaCognitiveAuditAgent(),)


def build_phase7_workers(*, allow_rupture_surface: bool = False):
    """Tạo worker Phase 7 Shadow/Rupture.

    Mặc định rupture không surface. Muốn public hóa rupture_candidate thì integration
    phải bật rõ `allow_rupture_surface=True` và vẫn để SurfaceArbiter/Permission xử.
    Không ai cần một con bot tự nhiên cay như comment section.
    """

    return (ShadowAccumulator(allow_rupture_surface=allow_rupture_surface),)



def build_phase6_workers(*, enable_commit: bool = False, committer: MutationCommitter | None = None):
    """Tạo worker Phase 6 Core Belief Mutation.

    Mặc định chỉ sinh proposal/pending fragment. Muốn commit thật phải bật
    ``enable_commit=True`` và tốt nhất truyền committer/store riêng. Phase 6 không
    được sửa thẳng agent_mind.json từ worker, vì đó là đường tắt dẫn tới loạn
    nhân cách có timestamp.
    """

    return (BeliefMutationEngine(enable_commit=enable_commit, committer=committer),)


def build_phase1234567_workers(
    *,
    allow_surface_memory_echo: bool = False,
    allow_daydream_surface: bool = False,
    allow_agenda_later_surface: bool = False,
    enable_mutation_commit: bool = False,
    mutation_committer: MutationCommitter | None = None,
    allow_rupture_surface: bool = False,
):
    """Tạo bộ worker Phase 1 → 7 đầy đủ.

    Thứ tự cố ý: Phase 1-4 sinh vật liệu, Phase 5 audit, Phase 6 đề xuất mutation,
    Phase 7 tích shadow. Commit mutation mặc định tắt, vì thay belief là việc cần
    bằng chứng dài ngày chứ không phải phản xạ sau một log đỏ.
    """

    return (
        *build_phase1234_workers(
            allow_surface_memory_echo=allow_surface_memory_echo,
            allow_daydream_surface=allow_daydream_surface,
            allow_agenda_later_surface=allow_agenda_later_surface,
        ),
        *build_phase5_workers(),
        *build_phase6_workers(enable_commit=enable_mutation_commit, committer=mutation_committer),
        *build_phase7_workers(allow_rupture_surface=allow_rupture_surface),
    )

def build_phase123457_workers(
    *,
    allow_surface_memory_echo: bool = False,
    allow_daydream_surface: bool = False,
    allow_agenda_later_surface: bool = False,
    allow_rupture_surface: bool = False,
):
    """Tạo bộ worker Phase 1 → 5 + 7, bỏ qua Phase 6 belief mutation.

    Phase 6 cố ý chưa nằm ở đây vì mutation cần cửa commit nhiều ngày. Làm shadow
    trước mutation được, nhưng phải có MetaAudit đứng canh. Không thì ERIS đổi đời
    sau một traceback, nghe hơi giống sinh viên IT thiếu ngủ nhưng không ổn định.
    """

    return (
        *build_phase1234_workers(
            allow_surface_memory_echo=allow_surface_memory_echo,
            allow_daydream_surface=allow_daydream_surface,
            allow_agenda_later_surface=allow_agenda_later_surface,
        ),
        *build_phase5_workers(),
        *build_phase7_workers(allow_rupture_surface=allow_rupture_surface),
    )


def build_default_subconscious_workers():
    """Alias an toàn cho runtime/dev smoke: Phase 1-7, không surface/commit tự phát."""

    return build_phase1234567_workers()
