package account

import (
	"hash/fnv"
	"sort"
	"strings"
)

// ResolveAffinity maps an opaque affinity key deterministically to exactly one
// live account id. It returns "" for an empty key or an empty pool so callers
// fall back to normal round-robin. To remain stable across turns where bumpQueue
// reorders p.queue, it hashes against a deterministic sorted slice of active
// account ids currently present in the pool.
func (p *Pool) ResolveAffinity(key string) string {
	if p == nil {
		return ""
	}
	key = strings.TrimSpace(key)
	if key == "" {
		return ""
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	n := len(p.queue)
	if n == 0 {
		return ""
	}
	stable := append([]string(nil), p.queue...)
	sort.Strings(stable)
	h := fnv.New64a()
	_, _ = h.Write([]byte(key))
	idx := h.Sum64() % uint64(n)
	return stable[idx]
}

