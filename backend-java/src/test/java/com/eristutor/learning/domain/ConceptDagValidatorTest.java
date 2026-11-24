package com.eristutor.learning.domain;

import com.eristutor.common.DomainValidationException;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.*;

class ConceptDagValidatorTest {

    @Test
    void validatesValidDag() {
        List<ConceptDagValidator.KnowledgePointSpec> nodes = List.of(
                new ConceptDagValidator.KnowledgePointSpec("kp:a", "A", "concept", List.of()),
                new ConceptDagValidator.KnowledgePointSpec("kp:b", "B", "procedure", List.of("kp:a")),
                new ConceptDagValidator.KnowledgePointSpec("kp:c", "C", "design", List.of("kp:a", "kp:b"))
        );
        assertThatCode(() -> ConceptDagValidator.validate("path:java-di", nodes)).doesNotThrowAnyException();
    }

    @Test
    void rejectsCycle() {
        List<ConceptDagValidator.KnowledgePointSpec> nodes = List.of(
                new ConceptDagValidator.KnowledgePointSpec("kp:a", "A", "concept", List.of("kp:b")),
                new ConceptDagValidator.KnowledgePointSpec("kp:b", "B", "concept", List.of("kp:a"))
        );
        assertThatThrownBy(() -> ConceptDagValidator.validate("path:cycle", nodes))
                .isInstanceOf(DomainValidationException.class)
                .hasMessageContaining("Cycle detected");
    }

    @Test
    void rejectsUnknownPrerequisiteAndDuplicate() {
        List<ConceptDagValidator.KnowledgePointSpec> unknown = List.of(
                new ConceptDagValidator.KnowledgePointSpec("kp:a", "A", "concept", List.of("kp:missing"))
        );
        assertThatThrownBy(() -> ConceptDagValidator.validate("path:u", unknown))
                .isInstanceOf(DomainValidationException.class);

        List<ConceptDagValidator.KnowledgePointSpec> dup = List.of(
                new ConceptDagValidator.KnowledgePointSpec("kp:a", "A1", "concept", List.of()),
                new ConceptDagValidator.KnowledgePointSpec("kp:a", "A2", "concept", List.of())
        );
        assertThatThrownBy(() -> ConceptDagValidator.validate("path:d", dup))
                .isInstanceOf(DomainValidationException.class);
    }

    @Test
    void rejectsOverBudget256Nodes() {
        List<ConceptDagValidator.KnowledgePointSpec> tooMany = new ArrayList<>();
        for (int i = 0; i < 257; i++) {
            tooMany.add(new ConceptDagValidator.KnowledgePointSpec("kp:" + i, "Node " + i, "fact", List.of()));
        }
        assertThatThrownBy(() -> ConceptDagValidator.validate("path:big", tooMany))
                .isInstanceOf(DomainValidationException.class)
                .hasMessageContaining("256");
    }
}
