package account

import (
	"context"
	"sync"
	"testing"
	"time"
)

// ─── ResolveAffinity ─────────────────────────────────────────────────

func TestResolveAffinityStableForFixedKey(t *testing.T) {
	pool := newPoolForTest(t, "1")

	first := pool.ResolveAffinity("conv-A")
	if first == "" {
		t.Fatal("expected non-empty resolved id for a fixed key")
	}
	for i := 0; i < 5; i++ {
		if got := pool.ResolveAffinity("conv-A"); got != first {
			t.Fatalf("affinity not stable at iteration %d: got %q want %q", i, got, first)
		}
	}
}

func TestResolveAffinityEmptyKeyReturnsEmpty(t *testing.T) {
	pool := newPoolForTest(t, "1")
	if got := pool.ResolveAffinity(""); got != "" {
		t.Fatalf("expected empty id for empty key, got %q", got)
	}
	if got := pool.ResolveAffinity("   "); got != "" {
		t.Fatalf("expected empty id for whitespace key, got %q", got)
	}
}

func TestResolveAffinityBusyAccountSoftQueues(t *testing.T) {
	pool := newPoolForTest(t, "1")

	resolvedID := pool.ResolveAffinity("conv-busy")
	if resolvedID == "" {
		t.Fatal("expected non-empty resolved id")
	}

	// Exhaust the resolved account at inflight=1.
	acc, ok := pool.Acquire(resolvedID, nil)
	if !ok {
		t.Fatalf("expected to acquire resolved account %q", resolvedID)
	}
	if acc.Identifier() != resolvedID {
		t.Fatalf("expected acquired id %q, got %q", resolvedID, acc.Identifier())
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	var wg sync.WaitGroup
	wg.Add(1)
	var waitID string
	var waitOK bool
	go func() {
		defer wg.Done()
		waited, ok := pool.AcquireWait(ctx, resolvedID, nil)
		waitID = waited.Identifier()
		waitOK = ok
	}()

	// The waiter should soft-queue because the resolved account is busy.
	waitForWaitingCount(t, pool, 1)

	// Releasing the busy account should let the queued waiter proceed.
	pool.Release(resolvedID)

	wg.Wait()
	if !waitOK {
		t.Fatal("expected queued waiter to succeed after release")
	}
	if waitID != resolvedID {
		t.Fatalf("expected waiter to acquire resolved account %q, got %q", resolvedID, waitID)
	}

	pool.Release(resolvedID)
}

func TestResolveAffinityStableAcrossAcquires(t *testing.T) {
	pool := newPoolForTest(t, "1")
	first := pool.ResolveAffinity("conv-loop")
	if first == "" {
		t.Fatal("expected non-empty resolved id")
	}

	for i := 0; i < 5; i++ {
		acc, ok := pool.Acquire(first, nil)
		if !ok {
			t.Fatalf("acquire iteration %d failed", i)
		}
		pool.Release(acc.Identifier())
		if got := pool.ResolveAffinity("conv-loop"); got != first {
			t.Fatalf("affinity shifted at iteration %d: got %q, want %q", i, got, first)
		}
	}
}
