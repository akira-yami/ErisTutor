package com.eristutor.learning;

import com.eristutor.learning.domain.MasteryEngine;
import com.eristutor.learning.persistence.MasteryRecordEntity;
import com.eristutor.learning.persistence.ReviewItemEntity;
import com.eristutor.security.SecurityUtils;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/learning")
@Tag(name = "Learning Core", description = "Deterministic Learning Ledger, Concept DAG, Mastery Engine, and SRS Review Scheduler")
public class LearningCoreController {

    private final LearningCoreService service;

    public LearningCoreController(LearningCoreService service) {
        this.service = service;
    }

    public record PathDto(
            @NotBlank String pathId,
            String title,
            String goal,
            String domainId,
            String source,
            String expectedRevision,
            String reason,
            List<LearningCoreService.KpInput> knowledgePoints
    ) {}

    public record AttemptDto(
            @NotBlank String attemptId,
            @NotBlank String kpId,
            @NotBlank String result,
            @NotBlank String evidenceType,
            Double strength,
            String interactionId,
            String turnId,
            String pathId,
            String itemId,
            String itemFamilyId,
            String activityId,
            String assistance,
            Integer hintCount,
            Boolean answerRevealed,
            Boolean reviewRecall,
            Integer rating,
            String errorCode,
            Double recordedAt
    ) {}

    public record CorrectDto(
            @NotBlank String correctionId,
            @NotBlank String result,
            String evidenceType,
            Double strength,
            String errorCode
    ) {}

    @PostMapping("/paths")
    @Operation(summary = "Register or revise a curriculum Concept DAG with CAS expectedRevision")
    public LearningCoreService.PathResponse registerOrRevisePath(@Valid @RequestBody PathDto dto) {
        return service.registerOrRevisePath(
                SecurityUtils.currentUserId(),
                new LearningCoreService.RegisterPathRequest(
                        dto.pathId(), dto.title(), dto.goal(), dto.domainId(),
                        dto.source(), dto.expectedRevision(), dto.reason(), dto.knowledgePoints()
                )
        );
    }

    @GetMapping("/paths")
    @Operation(summary = "List curriculum learning paths owned by the current learner")
    public List<LearningCoreService.PathResponse> listPaths() {
        return service.listPaths(SecurityUtils.currentUserId());
    }

    @GetMapping("/paths/{pathId}")
    @Operation(summary = "Get a curriculum path by ID")
    public LearningCoreService.PathResponse getPath(@PathVariable String pathId) {
        return service.getPath(SecurityUtils.currentUserId(), pathId);
    }

    @PostMapping("/attempts")
    @Operation(summary = "Append an idempotent assessment attempt and recompute deterministic mastery")
    public LearningCoreService.AttemptReceipt recordAttempt(@Valid @RequestBody AttemptDto dto) {
        return service.recordAttempt(
                SecurityUtils.currentUserId(),
                new LearningCoreService.RecordAttemptRequest(
                        dto.attemptId(), dto.kpId(), dto.result(), dto.evidenceType(),
                        dto.strength(), dto.interactionId(), dto.turnId(), dto.pathId(),
                        dto.itemId(), dto.itemFamilyId(), dto.activityId(), dto.assistance(),
                        dto.hintCount(), dto.answerRevealed(), dto.reviewRecall(), dto.rating(),
                        dto.errorCode(), dto.recordedAt()
                )
        );
    }

    @PostMapping("/attempts/{attemptId}/correct")
    @Operation(summary = "Supersede an existing attempt with an explicit correction preserving original timestamp")
    public LearningCoreService.AttemptReceipt correctAttempt(
            @PathVariable String attemptId,
            @Valid @RequestBody CorrectDto dto
    ) {
        return service.correctAttempt(
                SecurityUtils.currentUserId(),
                attemptId,
                new LearningCoreService.CorrectAttemptRequest(
                        dto.correctionId(), dto.result(), dto.evidenceType(), dto.strength(), dto.errorCode()
                )
        );
    }

    @DeleteMapping("/attempts/{attemptId}")
    @ResponseStatus(HttpStatus.OK)
    @Operation(summary = "Forget an attempt, write tombstone, and recompute mastery")
    public MasteryEngine.MasteryResult forgetAttempt(@PathVariable String attemptId) {
        return service.forgetAttempt(SecurityUtils.currentUserId(), attemptId);
    }

    @GetMapping("/mastery/{kpId}")
    @Operation(summary = "Get deterministic mastery projection for a knowledge point")
    public MasteryEngine.MasteryResult getMastery(@PathVariable String kpId) {
        return service.getMastery(SecurityUtils.currentUserId(), kpId);
    }

    @GetMapping("/mastery")
    @Operation(summary = "List all mastery snapshots for the current learner")
    public List<MasteryRecordEntity> listMastery() {
        return service.listMastery(SecurityUtils.currentUserId());
    }

    @GetMapping("/reviews/due")
    @Operation(summary = "List due spaced-repetition review items ordered by forgetting risk")
    public List<ReviewItemEntity> dueReviews() {
        return service.dueReviews(SecurityUtils.currentUserId());
    }

    @GetMapping("/context")
    @Operation(summary = "Read-only learner continuity summary for AI Tutor / Desktop Root")
    public Map<String, Object> learnerContext() {
        return service.getLearnerContextSummary(SecurityUtils.currentUserId());
    }
}
