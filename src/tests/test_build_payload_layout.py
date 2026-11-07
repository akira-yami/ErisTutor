"""Exercise payload copying without executing the destructive build entry points."""

import ast
from pathlib import Path
import shutil
import tempfile
import types
import unittest

from build_payload import source_copy_ignore


REPO = Path(__file__).resolve().parents[2]


class BuildPayloadLayoutTests(unittest.TestCase):
    def test_copy_keeps_runtime_and_model_but_excludes_development_debris(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "src"
            runtime_files = (
                "vision.py", "runtime/coordinator.py", "tests/test_runtime.py",
                "paraphrase-multilingual-MiniLM-L12-v2/config.json",
                "paraphrase-multilingual-MiniLM-L12-v2/model.safetensors",
                "assets/live2d/model.moc3", "body/richchat/vendor/mathjax.js",
            )
            debris = (
                "src.zip", "vscode-extension/node_modules/typescript/index.js",
                "runtime/__pycache__/coordinator.pyc", ".pytest_cache/cache.json",
                "assets/live2d/model.moc3.bak", "patches/old.patch", "old.diff",
            )
            for relative in (*runtime_files, *debris):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
            target = Path(temporary) / "payload"
            shutil.copytree(source, target, ignore=source_copy_ignore)
            copied = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
            self.assertEqual(copied, set(runtime_files))

    def test_generated_launcher_opens_eris_without_a_retired_sidecar(self):
        tree = ast.parse((REPO / "_fullbuild.py").read_text(encoding="utf-8-sig"))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "vbs_lines" for t in node.targets))
        launcher = "\n".join(ast.literal_eval(assignment.value))
        self.assertIn('shell.Run Q(root & "Eris.exe")', launcher)
        self.assertIn('root & "src\\vision.py"', launcher)
        self.assertNotIn("cline-bridge", launcher)
        self.assertNotIn("cmd.exe /c node", launcher)

    def test_setup_does_not_start_an_old_bridge_even_if_files_remain(self):
        tree = ast.parse((REPO / "eris_setup.py").read_text(encoding="utf-8-sig"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "start_all")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale = root / "cline-bridge/dist/bridge.js"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale sidecar", encoding="utf-8")

            def forbidden_spawn(*args, **kwargs):
                self.fail("Setup tried to start a retired sidecar")

            namespace = {
                "ROOT": root, "PORTS": {"whale2api": 9090}, "_procs": [],
                "banner": lambda _: None, "info": str, "warn": str,
                "print": lambda *args: None, "port_listening": lambda _: True,
                "time": types.SimpleNamespace(sleep=lambda _: None),
                "health_dashboard": lambda **kwargs: None,
                "subprocess": types.SimpleNamespace(Popen=forbidden_spawn),
            }
            exec(compile(ast.Module(body=[function], type_ignores=[]), "eris_setup.py", "exec"), namespace)
            namespace["start_all"]()
            self.assertEqual(namespace["_procs"], [])


if __name__ == "__main__":
    unittest.main()
