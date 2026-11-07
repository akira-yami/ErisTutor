package auth

import (
	"context"
	"net/http"
	"testing"

	"whale2api/internal/config"
)

func TestDetermineAffinityKeyPinsResolvedAccount(t *testing.T) {
	t.Setenv("WHALE2API_ACCOUNT_MAX_INFLIGHT", "2")
	r := newTestResolverWithAccounts(t, "managed-key", []config.Account{
		{Email: "acc1@test.com", Token: "t1"},
		{Email: "acc2@test.com", Token: "t2"},
		{Email: "acc3@test.com", Token: "t3"},
	}, func(_ context.Context, _ config.Account) (string, error) {
		return "fresh-token", nil
	})

	newRequest := func() *http.Request {
		req, _ := http.NewRequest("POST", "/", nil)
		req.Header.Set("Authorization", "Bearer managed-key")
		req.Header.Set("X-Whale2-Affinity-Key", "conversation-42")
		return req
	}

	// Prime the shared pool so we can read its live queue between calls.
	primed, err := r.Determine(newRequest())
	if err != nil {
		t.Fatalf("priming determine failed: %v", err)
	}
	r.poolsMu.Lock()
	pool := r.pools[callerTokenID("managed-key")]
	r.poolsMu.Unlock()
	if pool == nil {
		r.Release(primed)
		t.Fatal("expected a shared pool to be created for the managed key")
	}
	if primed.AccountID == "" {
		r.Release(primed)
		t.Fatal("expected affinity key to pin a concrete account")
	}
	r.Release(primed)

	// A lone affinity key must pin the request to exactly the account the live
	// pool resolves that key to. Acquiring rotates the round-robin queue, so the
	// resolved id is captured against the pool state immediately before each
	// Determine, confirming the Determine path feeds ResolveAffinity into the
	// target acquisition rather than falling back to round-robin.
	for i := 0; i < 3; i++ {
		want := pool.ResolveAffinity("conversation-42")
		if want == "" {
			t.Fatalf("iteration %d: expected affinity key to resolve to a concrete account", i)
		}
		a, err := r.Determine(newRequest())
		if err != nil {
			t.Fatalf("iteration %d: determine failed: %v", i, err)
		}
		if a.AccountID != want {
			r.Release(a)
			t.Fatalf("iteration %d: expected affinity key to pin %q, got %q", i, want, a.AccountID)
		}
		r.Release(a)
	}
}

func TestDetermineExplicitTargetWinsOverAffinityKey(t *testing.T) {
	t.Setenv("WHALE2API_ACCOUNT_MAX_INFLIGHT", "2")
	r := newTestResolverWithAccounts(t, "managed-key", []config.Account{
		{Email: "acc1@test.com", Token: "t1"},
		{Email: "acc2@test.com", Token: "t2"},
		{Email: "acc3@test.com", Token: "t3"},
	}, func(_ context.Context, _ config.Account) (string, error) {
		return "fresh-token", nil
	})

	req, _ := http.NewRequest("POST", "/", nil)
	req.Header.Set("Authorization", "Bearer managed-key")
	req.Header.Set("X-Whale2-Target-Account", "acc2@test.com")
	req.Header.Set("X-Whale2-Affinity-Key", "conversation-42")

	a, err := r.Determine(req)
	if err != nil {
		t.Fatalf("determine failed: %v", err)
	}
	defer r.Release(a)
	if a.AccountID != "acc2@test.com" {
		t.Fatalf("expected explicit target to win, got %q", a.AccountID)
	}
}
