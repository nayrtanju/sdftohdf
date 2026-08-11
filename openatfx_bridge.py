
from pathlib import Path
import os
import subprocess

class OpenATFXNotInstalled(RuntimeError):
    pass

def converter_command(root: Path) -> list[str]:
    dist = root / "tools" / "openatfx_mdf_converter"
    bin_dir = dist / "bin"
    if os.name == "nt":
        candidates = list(bin_dir.glob("*.bat"))
    else:
        candidates = [p for p in bin_dir.glob("*") if p.is_file()]

    if not candidates:
        raise OpenATFXNotInstalled(
            "openATFX MDF converter is not installed. Run: python setup_openatfx.py"
        )
    return [str(candidates[0])]

def create_atfx_header(mf4_path: str | Path, app_root: str | Path) -> Path:
    mf4_path = Path(mf4_path).resolve()
    app_root = Path(app_root).resolve()

    cmd = converter_command(app_root) + [str(mf4_path)]
    p = subprocess.run(
        cmd,
        cwd=mf4_path.parent,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(
            "openATFX MDF conversion failed.\n\nSTDOUT:\n"
            + (p.stdout or "")
            + "\nSTDERR:\n"
            + (p.stderr or "")
        )

    expected = mf4_path.with_suffix(".atfx")
    if expected.exists():
        return expected

    atfx_files = sorted(
        mf4_path.parent.glob("*.atfx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if atfx_files:
        return atfx_files[0]

    raise RuntimeError(
        "openATFX converter returned successfully but no .atfx file was found.\n"
        f"STDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
    )
