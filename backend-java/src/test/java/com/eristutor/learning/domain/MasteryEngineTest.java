package com.eristutor.learning.domain;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class MasteryEngineTest {

    @Test
    void hintedAndRevealedAttemptsNeverCountAsIndependentMastery() {
        double now = 1_700_000_000.0;
        var hinted = new MasteryEngine.EvidenceObservation(
                "ev1", "kp:di", "quiz_correct", 1.0, true, "i1", "t1",
                now - 10, "", "correct", "hinted", 1, false, "fam1"
        );
        var revealed = new MasteryEngine.EvidenceObservation(
                "ev2", "kp:di", "quiz_correct", 1.0, true, "i2", "t2",
                now - 5, "", "correct", "answer_revealed", 0, true, "fam2"
        );
        MasteryEngine.MasteryResult res = MasteryEngine.computeMastery("kp:di", List.of(hinted, revealed), now);
        assertThat(res.mastery()).isEqualTo(0.0);
        assertThat(res.correctCount()).isZero();
        assertThat(res.attemptCount()).isEqualTo(2);
    }

    @Test
    void independentEvidenceRespectsConfidenceCapAndMisconceptionDecay() {
        double now = 1_700_000_000.0;
        List<MasteryEngine.EvidenceObservation> evs = List.of(
                new MasteryEngine.EvidenceObservation("e1", "kp:di", "quiz_correct", 1.0, true, "i1", "t1", now - 60, "", "correct", "independent", 0, false, "f1"),
                new MasteryEngine.EvidenceObservation("e2", "kp:di", "quiz_correct", 1.0, true, "i2", "t2", now - 30, "", "correct", "independent", 0, false, "f2"),
                new MasteryEngine.EvidenceObservation("e3", "kp:di", "feynman_pass", 1.0, true, "i3", "t3", now - 10, "", "correct", "independent", 0, false, "f3")
        );
        MasteryEngine.MasteryResult high = MasteryEngine.computeMastery("kp:di", evs, now);
        assertThat(high.mastery()).isEqualTo(0.95); // capped at CONFIDENCE_CAP
        assertThat(high.stage()).isEqualTo("retained");
        assertThat(MasteryEngine.gatePassed(high, "concept", true)).isTrue();
        assertThat(MasteryEngine.gatePassed(high, "concept", false)).isFalse();

        var withMisconception = List.of(
                evs.get(0),
                new MasteryEngine.EvidenceObservation("e_err", "kp:di", "misconception", 0.0, false, "i4", "t4", now - 5, "NULL_PTR", "incorrect", "independent", 0, false, "f4")
        );
        MasteryEngine.MasteryResult decayed = MasteryEngine.computeMastery("kp:di", withMisconception, now);
        assertThat(decayed.mastery()).isLessThan(0.95);
        assertThat(decayed.errorCodes()).contains("NULL_PTR");
    }

    @Test
    void itemFamilyDeduplicationUsesLatestObservationPerFamily() {
        double now = 1_700_000_000.0;
        var oldFail = new MasteryEngine.EvidenceObservation("e1", "kp:di", "quiz_incorrect", 0.0, false, "i1", "t1", now - 100, "", "incorrect", "independent", 0, false, "famA");
        var newPass = new MasteryEngine.EvidenceObservation("e2", "kp:di", "quiz_correct", 1.0, true, "i2", "t2", now - 10, "", "correct", "independent", 0, false, "famA");

        MasteryEngine.MasteryResult res = MasteryEngine.computeMastery("kp:di", List.of(oldFail, newPass), now);
        assertThat(res.mastery()).isEqualTo(0.95);
        assertThat(res.correctCount()).isEqualTo(1);
    }
}
