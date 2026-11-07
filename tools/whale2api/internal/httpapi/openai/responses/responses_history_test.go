package responses

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"

	"whale2api/internal/auth"
	"whale2api/internal/chathistory"
	"whale2api/internal/config"
	dsclient "whale2api/internal/deepseek/client"
)

type responsesHistoryDS struct {
	payload map[string]any
	uploads []dsclient.UploadFileRequest
}

func (d *responsesHistoryDS) CreateSession(context.Context, *auth.RequestAuth, int) (string, error) {
	return "session-id", nil
}

func (d *responsesHistoryDS) GetPow(context.Context, *auth.RequestAuth, int) (string, error) {
	return "pow", nil
}

func (d *responsesHistoryDS) UploadFile(_ context.Context, _ *auth.RequestAuth, req dsclient.UploadFileRequest, _ int) (*dsclient.UploadFileResult, error) {
	d.uploads = append(d.uploads, req)
	return &dsclient.UploadFileResult{ID: "file-user-upload"}, nil
}

func (d *responsesHistoryDS) CallCompletion(_ context.Context, _ *auth.RequestAuth, payload map[string]any, _ string, _ int) (*http.Response, error) {
	d.payload = payload
	return &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader("data: {\"p\":\"response/content\",\"v\":\"ok\"}\n")),
	}, nil
}

func TestResponsesSendsFullConversationAsPlainText(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			store, resolver := newManagedKeyResolver(t)
			if err := store.Update(func(c *config.Config) error {
				enabled := true
				c.CurrentInputFile.Enabled = &enabled
				c.CurrentInputFile.MinChars = 1
				return nil
			}); err != nil {
				t.Fatalf("set legacy current input configuration: %v", err)
			}
			historyStore := chathistory.New(filepath.Join(t.TempDir(), "history.json"))
			ds := &responsesHistoryDS{}
			h := &Handler{Store: store, Auth: resolver, DS: ds, ChatHistory: historyStore}
			r := chi.NewRouter()
			RegisterRoutes(r, h)

			reqBody := fmt.Sprintf(`{"model":"deepseek-flash","stream":%t,"messages":[{"role":"system","content":"old response context"},{"role":"user","content":"find response language support"},{"role":"assistant","content":"I will search.","tool_calls":[{"id":"call_1","type":"function","function":{"name":"search_code","arguments":{"query":"response language support"}}}]},{"role":"tool","tool_call_id":"call_1","name":"search_code","content":"found: responses supports en, zh"}]}`, stream)
			req := httptest.NewRequest(http.MethodPost, "/v1/responses", strings.NewReader(reqBody))
			req.Header.Set("Authorization", "Bearer managed-key")
			req.Header.Set("Content-Type", "application/json")
			rec := httptest.NewRecorder()
			r.ServeHTTP(rec, req)

			if rec.Code != http.StatusOK {
				t.Fatalf("expected 200, got %d body=%s", rec.Code, rec.Body.String())
			}
			prompt, _ := ds.payload["prompt"].(string)
			previous := -1
			for _, text := range []string{
				"old response context",
				"find response language support",
				"I will search.",
				"::tc::",
				`::invoke name="search_code"::`,
				`::param name="query"::[[response language support]]::/param::`,
				"found: responses supports en, zh",
			} {
				position := strings.Index(prompt, text)
				if position <= previous {
					t.Fatalf("expected full conversation in order, missing or misplaced %q in %q", text, prompt)
				}
				previous = position
			}
			if len(ds.uploads) != 0 {
				t.Fatalf("expected no automatic transcript uploads, got %d", len(ds.uploads))
			}
			if refIDs, _ := ds.payload["ref_file_ids"].([]any); len(refIDs) != 0 {
				t.Fatalf("expected no generated file references, got %#v", refIDs)
			}
			snapshot, err := historyStore.Snapshot()
			if err != nil {
				t.Fatalf("snapshot history: %v", err)
			}
			if len(snapshot.Items) != 1 {
				t.Fatalf("expected one history item, got %d", len(snapshot.Items))
			}
			full, err := historyStore.Get(snapshot.Items[0].ID)
			if err != nil {
				t.Fatalf("get history item: %v", err)
			}
			if full.FinalPrompt != prompt {
				t.Fatalf("expected archived prompt to preserve the full upstream conversation, got %q", full.FinalPrompt)
			}
			if strings.TrimSpace(full.HistoryText) != "" {
				t.Fatalf("expected no separate transcript in response history, got %q", full.HistoryText)
			}
		})
	}
}

func TestResponsesPreservesExplicitFileInputs(t *testing.T) {
	store, resolver := newManagedKeyResolver(t)
	if err := store.Update(func(c *config.Config) error {
		enabled := true
		c.CurrentInputFile.Enabled = &enabled
		c.CurrentInputFile.MinChars = 1
		return nil
	}); err != nil {
		t.Fatalf("set legacy current input configuration: %v", err)
	}
	ds := &responsesHistoryDS{}
	h := &Handler{Store: store, Auth: resolver, DS: ds}
	r := chi.NewRouter()
	RegisterRoutes(r, h)

	reqBody := `{"model":"deepseek-flash","instructions":"keep explicit uploads","ref_file_ids":["file-existing"],"input":[{"role":"user","content":[{"type":"input_text","text":"read the supplied document"},{"type":"input_file","filename":"notes.txt","file_data":"data:text/plain;base64,aGVsbG8="}]}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1/responses", strings.NewReader(reqBody))
	req.Header.Set("Authorization", "Bearer managed-key")
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}
	if len(ds.uploads) != 1 {
		t.Fatalf("expected only the user-supplied file to be uploaded, got %d", len(ds.uploads))
	}
	if ds.uploads[0].Filename != "notes.txt" || string(ds.uploads[0].Data) != "hello" {
		t.Fatalf("expected the user-supplied file to be uploaded intact, got %#v", ds.uploads[0])
	}
	refIDs, _ := ds.payload["ref_file_ids"].([]any)
	if len(refIDs) != 2 || refIDs[0] != "file-existing" || refIDs[1] != "file-user-upload" {
		t.Fatalf("expected both explicit file references, got %#v", refIDs)
	}
	prompt, _ := ds.payload["prompt"].(string)
	if !strings.Contains(prompt, "keep explicit uploads") || !strings.Contains(prompt, "read the supplied document") {
		t.Fatalf("expected the full prompt alongside explicit file inputs, got %q", prompt)
	}
}

func (d *responsesHistoryDS) DeleteSessionForToken(context.Context, string, string) (*dsclient.DeleteSessionResult, error) {
	return &dsclient.DeleteSessionResult{Success: true}, nil
}

func (d *responsesHistoryDS) DeleteAllSessionsForToken(context.Context, string) error {
	return nil
}

func TestResponsesRecordsResponseHistory(t *testing.T) {
	store, resolver := newManagedKeyResolver(t)
	historyStore := chathistory.New(filepath.Join(t.TempDir(), "history.json"))
	ds := &responsesHistoryDS{}
	h := &Handler{
		Store:       store,
		Auth:        resolver,
		DS:          ds,
		ChatHistory: historyStore,
	}
	r := chi.NewRouter()
	RegisterRoutes(r, h)

	req := httptest.NewRequest(http.MethodPost, "/v1/responses", strings.NewReader(`{"model":"deepseek-flash","input":"hello responses"}`))
	req.Header.Set("Authorization", "Bearer managed-key")
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}
	if ds.payload == nil {
		t.Fatalf("expected upstream payload to be sent")
	}
	snapshot, err := historyStore.Snapshot()
	if err != nil {
		t.Fatalf("snapshot history: %v", err)
	}
	if len(snapshot.Items) != 1 {
		t.Fatalf("expected one history item, got %d", len(snapshot.Items))
	}
	item, err := historyStore.Get(snapshot.Items[0].ID)
	if err != nil {
		t.Fatalf("get history item: %v", err)
	}
	if item.Surface != "openai.responses" {
		t.Fatalf("unexpected surface: %q", item.Surface)
	}
	if !strings.Contains(item.UserInput, "hello responses") {
		t.Fatalf("unexpected user input: %q", item.UserInput)
	}
	if strings.TrimSpace(item.HistoryText) != "" {
		t.Fatalf("expected no separate transcript in response history, got %q", item.HistoryText)
	}
	if item.Content != "ok" {
		t.Fatalf("expected raw upstream content, got %q", item.Content)
	}
}
