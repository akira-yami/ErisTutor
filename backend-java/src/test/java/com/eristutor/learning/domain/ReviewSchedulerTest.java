package com.eristutor.learning.domain;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class ReviewSchedulerTest {

    @Test
    void retrievabilityDecaysOverTimeAndSm2GrowsIntervalOnRecall() {
        double now = 1_700_000_000.0;
        double fresh = ReviewScheduler.computeRetrievability(now - 60, 2, now);
        double aged = ReviewScheduler.computeRetrievability(now - 20 * 86400.0, 0, now);
        assertThat(fresh).isGreaterThan(0.99);
        assertThat(aged).isLessThan(0.30);

        ReviewScheduler.ReviewSnapshot first = ReviewScheduler.nextReviewItem("kp:a", now, 1, null, 3, now);
        assertThat(first.intervalDays()).isEqualTo(2.5);
        assertThat(first.dueAt()).isGreaterThan(now);

        ReviewScheduler.ReviewSnapshot failed = ReviewScheduler.nextReviewItem("kp:a", now, 1, first, 1, now + 86400.0);
        assertThat(failed.intervalDays()).isEqualTo(1.0);
        assertThat(failed.easeFactor()).isLessThan(first.easeFactor());
    }
}
