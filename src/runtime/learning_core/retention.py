"""Optional py-fsrs adapter: deterministic replay, retained as a shadow projection."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from .records import digest, evidence_metadata, independent

FSRS_VERSION = "6.3.2"


def readiness():
    try:
        actual = version("fsrs")
    except PackageNotFoundError:
        return {"available": False, "reason": "fsrs_extra_not_installed", "mode": "shadow"}
    if actual != FSRS_VERSION:
        return {"available": False, "version": actual, "reason": "fsrs_version_mismatch", "mode": "shadow"}
    try:
        from fsrs import Card, Rating, Scheduler  # noqa: F401
    except (ImportError, OSError):
        return {"available": False, "version": actual, "reason": "fsrs_import_failed", "mode": "shadow"}
    return {"available": True, "version": actual, "reason": "", "mode": "shadow"}


def replay(evidences, *, desired_retention=0.9):
    """No SQLite, model, current clock or random IDs. Old heuristic state is never imported."""
    if not readiness()["available"]:
        return {}
    from fsrs import Card, Rating, Scheduler

    scheduler = Scheduler(
        desired_retention=desired_retention, enable_fuzzing=False, learning_steps=(), relearning_steps=()
    )
    cards, logs, skills, seen = {}, {}, {}, set()
    for evidence in sorted(evidences, key=lambda e: (e.recorded_at, e.evidence_id)):
        meta = evidence_metadata(evidence)
        # Legacy entries lack an explicit result, assistance and item identity.
        # Do not invent review history by migrating them as successful recalls.
        if (
            meta.get("review_recall") is not True
            or not independent(evidence)
            or meta.get("result") not in {"correct", "incorrect"}
        ):
            continue
        family = meta.get("item_family_id") or evidence.interaction_id
        if not family:
            continue
        event = (evidence.kp_id, evidence.interaction_id or evidence.turn_id)
        if event in seen:
            continue
        seen.add(event)
        item_id = "review:" + digest([evidence.kp_id, family])[:32]
        instant = datetime.fromtimestamp(evidence.recorded_at, timezone.utc)
        card = cards.get(item_id) or Card(card_id=int(digest(item_id)[:13], 16), due=instant)
        raw_rating = meta.get("rating", 0)
        # Hard is remembered with difficulty, never a failed recall.
        rating = 1 if not evidence.correct else (raw_rating if raw_rating in {2, 3, 4} else 3)
        card, log = scheduler.review_card(card, Rating(rating), review_datetime=instant)
        cards[item_id] = card
        skills[item_id] = evidence.kp_id
        logs.setdefault(item_id, []).append({"evidence_id": evidence.evidence_id, **log.to_dict()})
    return {
        key: {
            "scheduler_version": FSRS_VERSION,
            "mode": "shadow",
            "kp_id": skills[key],
            "desired_retention": desired_retention,
            "card": card.to_dict(),
            "logs": logs[key],
        }
        for key, card in cards.items()
    }


def schedule_view(items, *, now):
    """Earliest due item per skill; this projection never updates a Card."""
    if not readiness()["available"]:
        return {}
    from fsrs import Card, Scheduler

    result = {}
    instant = datetime.fromtimestamp(now, timezone.utc)
    for value in items.values():
        card = Card.from_dict(value["card"])
        kp_id = value.get("kp_id")
        if not kp_id:
            continue
        scheduler = Scheduler(desired_retention=value["desired_retention"], enable_fuzzing=False)
        view = {
            "due_at": card.due.timestamp(),
            "retrievability": scheduler.get_card_retrievability(card, current_datetime=instant),
            "interval_days": max(0.0, (card.due - card.last_review).total_seconds() / 86400)
            if card.last_review
            else 0.0,
        }
        if kp_id not in result or view["due_at"] < result[kp_id]["due_at"]:
            result[kp_id] = view
    return result
