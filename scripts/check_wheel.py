"""Install the built wheel without source imports and exercise its stdio server."""

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    from jasminetea_mcp import __version__
    wheels = list((root / "dist").glob(f"jasminetea_mcp-{__version__}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one wheel in dist; build it first.")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(prefix="jasminetea-wheel-") as temporary:
        workspace = Path(temporary)
        virtualenv = workspace / "venv"
        venv.EnvBuilder(with_pip=False).create(virtualenv)
        python = virtualenv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [sys.executable, "-m", "pip", "--python", str(python), "install", str(wheels[0])],
            cwd=workspace, env=environment, check=True,
        )
        subprocess.run(
            [str(python), "-I", "-c",
             "import json,sys; from pathlib import Path; import jasminetea_mcp as m; "
             "assert Path(m.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
             "assert len(json.loads(Path(m.__file__).with_name('parity.json').read_text(encoding='utf-8')))==89; "
             "assert '/user' in json.loads(Path(m.__file__).with_name('basic-read-schema.json').read_text(encoding='utf-8'))['paths']; "
             "print('Installed wheel:', m.__version__)"],
            cwd=workspace, env=environment, check=True,
        )
        subprocess.run(
            [str(python), "-I", "-m", "jasminetea_mcp", "--version"],
            cwd=workspace, env=environment, check=True,
        )
        environment["JASMINETEA_TEST_PYTHON"] = str(python)
        subprocess.run(
            [sys.executable, "-m", "pytest", str(root / "tests/test_stdio.py"),
             "-c", str(root / "pyproject.toml"), "--basetemp", str(workspace / "pytest"), "-q"],
            cwd=workspace, env=environment, check=True,
        )


if __name__ == "__main__":
    main()
