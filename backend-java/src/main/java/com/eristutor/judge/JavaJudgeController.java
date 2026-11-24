package com.eristutor.judge;

import com.eristutor.security.SecurityUtils;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.List;

@RestController
@RequestMapping("/api/v1/judge")
@Tag(name = "Java Code Judge", description = "Knowledge Packs and Sandboxed Docker Java 21 Code Execution")
public class JavaJudgeController {

    private final JavaJudgeService judgeService;
    private final KnowledgePackRegistry packRegistry;

    public JavaJudgeController(JavaJudgeService judgeService, KnowledgePackRegistry packRegistry) {
        this.judgeService = judgeService;
        this.packRegistry = packRegistry;
    }

    public record SubmitRequest(
            @NotBlank String requestId,
            String packId,
            @NotBlank String nodeId,
            @NotBlank String exerciseId,
            @NotBlank String code,
            String assistance,
            Integer hintCount,
            Boolean answerRevealed,
            Double timeoutSeconds,
            Boolean commitToLearning,
            JavaJudgeService.InlineExercise inlineExercise
    ) {}

    @GetMapping("/packs")
    @Operation(summary = "List loaded curriculum knowledge packs")
    public List<KnowledgePackRegistry.PackSpec> listPacks() {
        return packRegistry.listPacks();
    }

    @GetMapping("/packs/{groupId}")
    @Operation(summary = "Get a curriculum pack by groupId (e.g. java-di)")
    public KnowledgePackRegistry.PackSpec getPack(@PathVariable String groupId) {
        return packRegistry.getPack(groupId);
    }

    @GetMapping("/java/readiness")
    @Operation(summary = "Check whether Docker Engine and JDK sandbox image are ready")
    public DockerJavaRunner.ReadinessStatus readiness() {
        return judgeService.readiness();
    }

    @PostMapping("/java/submissions")
    @Operation(summary = "Submit Java code for sandboxed Docker compilation and execution (async 202 or optional bounded wait)")
    public ResponseEntity<JavaJudgeService.JudgeJobView> submit(
            @Valid @RequestBody SubmitRequest req,
            @RequestParam(defaultValue = "0") int waitSeconds
    ) {
        JavaJudgeService.JudgeJobView view = judgeService.submit(
                SecurityUtils.currentUserId(),
                new JavaJudgeService.SubmitJavaRequest(
                        req.requestId(), req.packId(), req.nodeId(), req.exerciseId(), req.code(),
                        req.assistance(), req.hintCount(), req.answerRevealed(),
                        req.timeoutSeconds(), req.commitToLearning(), req.inlineExercise()
                ),
                waitSeconds
        );
        HttpStatus status = "running".equals(view.status()) ? HttpStatus.ACCEPTED : HttpStatus.OK;
        return ResponseEntity.status(status).body(view);
    }

    @GetMapping("/java/submissions/{jobId}")
    @Operation(summary = "Fetch current status and test verdicts of a Java judge submission")
    public JavaJudgeService.JudgeJobView getSubmission(@PathVariable String jobId) {
        return judgeService.getJob(SecurityUtils.currentUserId(), jobId);
    }

    @GetMapping(value = "/java/submissions/{jobId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @Operation(summary = "Stream live SSE completion event for a Java judge submission")
    public SseEmitter streamEvents(@PathVariable String jobId) {
        return judgeService.subscribe(SecurityUtils.currentUserId(), jobId);
    }
}
