// Restored Int64 cooldown parsing contract for Settings Panel.
public partial class UdpListener
{
    public sealed class ProviderPoolView
    {
        public long CooldownMs { get; set; }
    }

    private static long ReadPoolCooldown(dynamic pool) => ReadJsonInt64(pool, "global_cooldown_ms", 0L);
    private static long ReadKeyCooldown(dynamic key) => ReadJsonInt64(key, "cooldown_ms", 0L);
    private static long ReadJsonInt64(dynamic node, string name, long fallback) => fallback;
}
