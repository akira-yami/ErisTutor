import os
import subprocess
import sys


WORKDIR = os.path.dirname(os.path.abspath(__file__))
PYTEST_BASE_ARGS = ["--tb=line", "-q", "--no-header"]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    src_dir = os.path.join(WORKDIR, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    target_args = sys.argv[1:] if len(sys.argv) > 1 else ["tests/"]
    cmd = [sys.executable, "-m", "pytest", *target_args, *PYTEST_BASE_ARGS]
    print(f"[TEST RUNNER] Running: {' '.join(cmd)}")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
