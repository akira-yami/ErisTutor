import sys

audio_modules = [
    "runtime.prosody_director",
    "runtime.audio_lip_sync",
    "runtime.speech_localizer",
    "runtime.speech_persona_polish",
    "runtime.tts_audio_polish",
    "runtime.tts_markup",
    "runtime.vocal_cache",
    "runtime.speech_contract",
    "runtime.addressing_policy",
    "runtime.user_addressing_state",
    "runtime.tts_circuit_breaker",
    "runtime.tts_key_health",
    "runtime.elevenlabs_pool_db",
    "runtime.elevenlabs_pool_server",
    "runtime.voice_director",
    "runtime.tts_budget",
    "runtime.hardening_config",
    "runtime.speech_provider_chain",
    "runtime.tts_engine",
]

failed = []
for mod in audio_modules:
    try:
        __import__(mod)
        print(f"OK   {mod}")
    except Exception as exc:
        print(f"FAIL {mod}: {exc}")
        failed.append((mod, exc))

if failed:
    print(f"\n{len(failed)} module(s) failed to import.")
    sys.exit(1)
else:
    print("\nAll runtime audio/TTS modules imported cleanly.")
