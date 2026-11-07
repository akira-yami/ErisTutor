package completionruntime

import (
	"context"
	"io"
	"net/http"
	"strings"

	"whale2api/internal/assistantturn"
	"whale2api/internal/auth"
	"whale2api/internal/config"
	"whale2api/internal/httpapi/openai/shared"
	"whale2api/internal/promptcompat"
	"whale2api/internal/sse"
)

type DeepSeekCaller interface {
	CreateSession(ctx context.Context, a *auth.RequestAuth, maxAttempts int) (string, error)
	GetPow(ctx context.Context, a *auth.RequestAuth, maxAttempts int) (string, error)
	CallCompletion(ctx context.Context, a *auth.RequestAuth, payload map[string]any, powResp string, maxAttempts int) (*http.Response, error)
}

type Options struct {
	StripReferenceMarkers bool
	MaxAttempts           int
	RetryEnabled          bool
	RetryMaxAttempts      int
	RebuildRequest        func(context.Context, *auth.RequestAuth) (promptcompat.StandardRequest, error)
	// ClientHTTPRequestContentLength, when non-nil, is included in upstream_empty_output logs (see net/http: -1 if unknown).
	ClientHTTPRequestContentLength *int64
}

type NonStreamResult struct {
	SessionID string
	Payload   map[string]any
	Turn      assistantturn.Turn
	Attempts  int
}

type StartResult struct {
	SessionID string
	Payload   map[string]any
	Pow       string
	Response  *http.Response
	Request   promptcompat.StandardRequest
}

func StartCompletion(ctx context.Context, ds DeepSeekCaller, a *auth.RequestAuth, stdReq promptcompat.StandardRequest, opts Options) (StartResult, *assistantturn.OutputError) {
	if gateErr := userContextOverGate(stdReq); gateErr != nil {
		return StartResult{Request: stdReq}, gateErr
	}
	for {
		result, outErr := startCompletionAttempt(ctx, ds, a, stdReq, opts)
		if outErr == nil || a == nil || !a.CurrentAccountDiscarded() {
			return result, outErr
		}
		oldAccount := a.AccountID
		if !a.SwitchAccount(ctx) {
			return result, outErr
		}
		config.Logger.Info("[completion_runtime] restarting with replacement account", "old_account", oldAccount, "new_account", a.AccountID)
		if opts.RebuildRequest != nil {
			for {
				rebuilt, err := opts.RebuildRequest(ctx, a)
				if err == nil {
					stdReq = rebuilt
					break
				}
				if !a.CurrentAccountDiscarded() || !a.SwitchAccount(ctx) {
					return StartResult{Request: stdReq}, &assistantturn.OutputError{Status: http.StatusInternalServerError, Message: "Failed to rebuild request for replacement account.", Code: "error"}
				}
			}
		}
	}
}

func startCompletionAttempt(ctx context.Context, ds DeepSeekCaller, a *auth.RequestAuth, stdReq promptcompat.StandardRequest, opts Options) (StartResult, *assistantturn.OutputError) {
	maxAttempts := opts.MaxAttempts
	if maxAttempts <= 0 {
		maxAttempts = 3
	}
	sessionID, err := ds.CreateSession(ctx, a, maxAttempts)
	if err != nil {
		return StartResult{Request: stdReq}, authOutputError(a)
	}
	pow, err := ds.GetPow(ctx, a, maxAttempts)
	if err != nil {
		return StartResult{SessionID: sessionID, Request: stdReq}, &assistantturn.OutputError{Status: http.StatusUnauthorized, Message: "Failed to get PoW (invalid token or unknown error).", Code: "error"}
	}
	payload := stdReq.CompletionPayload(sessionID)
	resp, err := ds.CallCompletion(ctx, a, payload, pow, maxAttempts)
	if err != nil {
		return StartResult{SessionID: sessionID, Payload: payload, Pow: pow, Request: stdReq}, &assistantturn.OutputError{Status: http.StatusInternalServerError, Message: "Failed to get completion.", Code: "error"}
	}
	return StartResult{SessionID: sessionID, Payload: payload, Pow: pow, Response: resp, Request: stdReq}, nil
}

// ExecuteNonStreamWithRetry runs a completion and, on a terminal
// upstream_empty_output that same-account retries could not clear, rotates to
// the next pool account and tries again. An empty stream arrives over HTTP 200,
// so it never trips the mute/ban auto-discard path that normally drives account
// switching; without this loop a single rate-limited/empty account would return
// 429 to the client while the rest of the pool sits idle.
func ExecuteNonStreamWithRetry(ctx context.Context, ds DeepSeekCaller, a *auth.RequestAuth, stdReq promptcompat.StandardRequest, opts Options) (NonStreamResult, *assistantturn.OutputError) {
	for {
		result, outErr := executeNonStreamOnce(ctx, ds, a, stdReq, opts)
		if !isEmptyOutputError(outErr) || a == nil {
			return result, outErr
		}
		// Same-account retries are exhausted. Mark this account tried and move to
		// the next one before surfacing the empty-output 429 to the client.
		oldAccount := a.AccountID
		if !a.SwitchAccount(ctx) {
			return result, outErr
		}
		config.Logger.Info("[completion_runtime] rotating account after empty output", "old_account", oldAccount, "new_account", a.AccountID, "surface", stdReq.Surface)
		if opts.RebuildRequest != nil {
			rebuilt, err := rebuildForAccount(ctx, a, opts)
			if err != nil {
				return result, outErr
			}
			stdReq = rebuilt
		}
	}
}

// rebuildForAccount rebuilds the request for a replacement account, skipping
// further accounts that fail their own rebuild, mirroring StartCompletion.
func rebuildForAccount(ctx context.Context, a *auth.RequestAuth, opts Options) (promptcompat.StandardRequest, error) {
	for {
		rebuilt, err := opts.RebuildRequest(ctx, a)
		if err == nil {
			return rebuilt, nil
		}
		if !a.SwitchAccount(ctx) {
			return promptcompat.StandardRequest{}, err
		}
	}
}

// isEmptyOutputError reports whether the terminal error is an upstream empty
// output (the HTTP 200 / empty-stream case), which warrants an account rotation.
func isEmptyOutputError(outErr *assistantturn.OutputError) bool {
	return outErr != nil && outErr.Code == "upstream_empty_output"
}

func executeNonStreamOnce(ctx context.Context, ds DeepSeekCaller, a *auth.RequestAuth, stdReq promptcompat.StandardRequest, opts Options) (NonStreamResult, *assistantturn.OutputError) {
	start, startErr := StartCompletion(ctx, ds, a, stdReq, opts)
	if startErr != nil {
		return NonStreamResult{SessionID: start.SessionID, Payload: start.Payload}, startErr
	}
	stdReq = start.Request
	maxAttempts := opts.MaxAttempts
	if maxAttempts <= 0 {
		maxAttempts = 3
	}
	sessionID := start.SessionID
	payload := start.Payload
	pow := start.Pow

	attempts := 0
	currentResp := start.Response
	usagePrompt := stdReq.PromptTokenText
	accumulatedThinking := ""
	accumulatedRawThinking := ""
	accumulatedToolDetectionThinking := ""
	for {
		turn, outErr := collectAttempt(ctx, a, currentResp, stdReq, usagePrompt, opts)
		if outErr != nil {
			return NonStreamResult{SessionID: sessionID, Payload: payload, Attempts: attempts}, outErr
		}
		accumulatedThinking += sse.TrimContinuationOverlap(accumulatedThinking, turn.Thinking)
		accumulatedRawThinking += sse.TrimContinuationOverlap(accumulatedRawThinking, turn.RawThinking)
		accumulatedToolDetectionThinking += sse.TrimContinuationOverlap(accumulatedToolDetectionThinking, turn.DetectionThinking)
		turn.Thinking = accumulatedThinking
		turn.RawThinking = accumulatedRawThinking
		turn.DetectionThinking = accumulatedToolDetectionThinking
		turn = assistantturn.BuildTurnFromCollected(sse.CollectResult{
			Text:                  turn.RawText,
			Thinking:              turn.RawThinking,
			ToolDetectionThinking: turn.DetectionThinking,
			ContentFilter:         turn.ContentFilter,
			CitationLinks:         turn.CitationLinks,
			ResponseMessageID:     turn.ResponseMessageID,
		}, buildOptions(stdReq, usagePrompt, opts))

		retryMax := opts.RetryMaxAttempts
		if retryMax <= 0 {
			retryMax = shared.EmptyOutputRetryMaxAttempts()
		}
		if !opts.RetryEnabled || !assistantturn.ShouldRetryIncompleteAssistantTurn(turn, len(stdReq.ToolNames) > 0, attempts, retryMax) {
			if turn.Error != nil && turn.Error.Code == "upstream_empty_output" {
				assistantturn.LogUpstreamEmptyOutputDiagnostic(stdReq.Surface, false, "nonstream_terminal", turn, payload, opts.ClientHTTPRequestContentLength)
			}
			return NonStreamResult{SessionID: sessionID, Payload: payload, Turn: turn, Attempts: attempts}, turn.Error
		}

		attempts++
		toolsAvailable := len(stdReq.ToolNames) > 0
		suffix := shared.RetrySuffixForTurn(turn.Text, toolsAvailable)
		priorTurn := turn
		config.Logger.Info("[completion_runtime_empty_retry] attempting synthetic retry", "surface", stdReq.Surface, "stream", false, "retry_attempt", attempts, "parent_message_id", turn.ResponseMessageID, "retry_reason", shared.RetryReasonLabel(turn.Text, toolsAvailable))
		retryPow, powErr := ds.GetPow(ctx, a, maxAttempts)
		if powErr != nil {
			config.Logger.Warn("[completion_runtime_empty_retry] retry PoW fetch failed, falling back to original PoW", "surface", stdReq.Surface, "retry_attempt", attempts, "error", powErr)
			retryPow = pow
		}
		retryPayload := shared.ClonePayloadForAssistantRetry(payload, turn.ResponseMessageID, suffix)
		nextResp, err := ds.CallCompletion(ctx, a, retryPayload, retryPow, maxAttempts)
		if err != nil {
			return NonStreamResult{SessionID: sessionID, Payload: payload, Turn: turn, Attempts: attempts}, &assistantturn.OutputError{Status: http.StatusInternalServerError, Message: "Failed to get completion.", Code: "error"}
		}
		usagePrompt = shared.UsagePromptWithRetrySuffix(usagePrompt, attempts, suffix)
		currentResp = nextResp
		retryTurn, outErr := collectAttempt(ctx, a, currentResp, stdReq, usagePrompt, opts)
		if outErr != nil {
			return NonStreamResult{SessionID: sessionID, Payload: payload, Turn: priorTurn, Attempts: attempts}, outErr
		}
		accumulatedThinking += sse.TrimContinuationOverlap(accumulatedThinking, retryTurn.Thinking)
		accumulatedRawThinking += sse.TrimContinuationOverlap(accumulatedRawThinking, retryTurn.RawThinking)
		accumulatedToolDetectionThinking += sse.TrimContinuationOverlap(accumulatedToolDetectionThinking, retryTurn.DetectionThinking)
		retryTurn.Thinking = accumulatedThinking
		retryTurn.RawThinking = accumulatedRawThinking
		retryTurn.DetectionThinking = accumulatedToolDetectionThinking
		retryTurn = assistantturn.BuildTurnFromCollected(sse.CollectResult{
			Text:                  retryTurn.RawText,
			Thinking:              retryTurn.RawThinking,
			ToolDetectionThinking: retryTurn.DetectionThinking,
			ContentFilter:         retryTurn.ContentFilter,
			CitationLinks:         retryTurn.CitationLinks,
			ResponseMessageID:     retryTurn.ResponseMessageID,
		}, buildOptions(stdReq, usagePrompt, opts))
		if len(retryTurn.ToolCalls) == 0 && strings.TrimSpace(priorTurn.Text) != "" {
			priorTurn.ResponseMessageID = retryTurn.ResponseMessageID
			return NonStreamResult{SessionID: sessionID, Payload: payload, Turn: priorTurn, Attempts: attempts}, priorTurn.Error
		}
		if retryTurn.Error != nil && retryTurn.Error.Code == "upstream_empty_output" {
			assistantturn.LogUpstreamEmptyOutputDiagnostic(stdReq.Surface, false, "nonstream_terminal", retryTurn, payload, opts.ClientHTTPRequestContentLength)
		}
		return NonStreamResult{SessionID: sessionID, Payload: payload, Turn: retryTurn, Attempts: attempts}, retryTurn.Error
	}
}

func collectAttempt(ctx context.Context, a *auth.RequestAuth, resp *http.Response, stdReq promptcompat.StandardRequest, usagePrompt string, opts Options) (assistantturn.Turn, *assistantturn.OutputError) {
	defer func() {
		if err := resp.Body.Close(); err != nil {
			config.Logger.Warn("[completion_runtime] response body close failed", "surface", stdReq.Surface, "error", err)
		}
	}()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		if a != nil {
			a.TryAutoDiscardHTTPBody(ctx, body)
		}
		message := strings.TrimSpace(string(body))
		if message == "" {
			message = http.StatusText(resp.StatusCode)
		}
		return assistantturn.Turn{}, &assistantturn.OutputError{Status: resp.StatusCode, Message: message, Code: "error"}
	}
	result := sse.CollectStream(resp, stdReq.Thinking, false)
	return assistantturn.BuildTurnFromCollected(result, buildOptions(stdReq, usagePrompt, opts)), nil
}

func buildOptions(stdReq promptcompat.StandardRequest, prompt string, opts Options) assistantturn.BuildOptions {
	return assistantturn.BuildOptions{
		Model:                 stdReq.ResponseModel,
		Prompt:                prompt,
		RefFileTokens:         stdReq.RefFileTokens,
		SearchEnabled:         stdReq.Search,
		StripReferenceMarkers: opts.StripReferenceMarkers,
		ToolNames:             stdReq.ToolNames,
		ToolsRaw:              stdReq.ToolsRaw,
		ToolChoice:            stdReq.ToolChoice,
	}
}

func authOutputError(a *auth.RequestAuth) *assistantturn.OutputError {
	if a != nil && a.UseConfigToken {
		return &assistantturn.OutputError{Status: http.StatusUnauthorized, Message: "Account token is invalid. Please re-login the account in pool UI.", Code: "error"}
	}
	return &assistantturn.OutputError{Status: http.StatusUnauthorized, Message: "Invalid token. If this should be a Whale2API key, add it to config.keys first.", Code: "error"}
}
