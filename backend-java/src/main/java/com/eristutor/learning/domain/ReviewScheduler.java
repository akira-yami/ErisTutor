package com.eristutor.learning.domain;

import java.util.Map;

public final class ReviewScheduler {

    public static final double DEFAULT_INTERVAL_DAYS = 1.0;
    public static final double DEFAULT_EASE = 2.5;
    public static final double MIN_EASE = 1.3;
    public static final double MAX_EASE = 5.0;
    public static final double HALF_LIFE_DAYS = 14.0;
    public static final double MAX_INTERVAL_DAYS = 180.0;

    private static final Map<Integer, Double> EASE_DELTA = Map.of(
            0, -0.3,
            1, -0.2,
            2, -0.1,
            3, 0.0,
            4, 0.1
    );

    private ReviewScheduler() {}

    public record ReviewSnapshot(
            String kpId,
            double dueAt,
            double forgettingRisk,
            double intervalDays,
            double easeFactor
    ) {}

    public static double computeRetrievability(double lastEvidenceAt, int correctCount, double nowSeconds) {
        if (lastEvidenceAt <= 0.0) {
            return 1.0;
        }
        double ageDays = Math.max(0.0, (nowSeconds - lastEvidenceAt) / 86400.0);
        double stability = Math.max(0.5, Math.min(10.0, 1.0 + correctCount * 0.5));
        double halfLife = HALF_LIFE_DAYS * stability;
        return Math.exp(-ageDays / halfLife);
    }

    public static ReviewSnapshot nextReviewItem(
            String kpId,
            double lastEvidenceAt,
            int correctCount,
            ReviewSnapshot currentItem,
            int quality,
            double nowSeconds
    ) {
        int q = Math.max(0, Math.min(4, quality));
        double prevInterval = currentItem != null ? currentItem.intervalDays() : DEFAULT_INTERVAL_DAYS;
        double prevEase = currentItem != null ? currentItem.easeFactor() : DEFAULT_EASE;

        double delta = EASE_DELTA.getOrDefault(q, 0.0);
        double newEase = Math.max(MIN_EASE, Math.min(MAX_EASE, prevEase + delta));

        double newInterval;
        if (q < 2) {
            newInterval = DEFAULT_INTERVAL_DAYS;
        } else if (q == 2) {
            newInterval = prevInterval;
        } else {
            newInterval = Math.min(MAX_INTERVAL_DAYS, prevInterval * newEase);
        }
        newInterval = Math.max(0.1, newInterval);
        double dueAt = nowSeconds + newInterval * 86400.0;

        double retrievability = computeRetrievability(lastEvidenceAt, correctCount, nowSeconds);
        double forgettingRisk = Math.max(0.0, 1.0 - retrievability);

        return new ReviewSnapshot(
                kpId,
                dueAt,
                round4(forgettingRisk),
                Math.round(newInterval * 100.0) / 100.0,
                Math.round(newEase * 1000.0) / 1000.0
        );
    }

    private static double round4(double v) {
        return Math.round(v * 10000.0) / 10000.0;
    }
}
