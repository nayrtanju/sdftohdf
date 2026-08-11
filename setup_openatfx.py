
from pathlib import Path
import os
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
BUILD_ROOT = ROOT / "_openatfx_build"

ECLIPSE_ARCHIVE = (
    "https://gitlab.eclipse.org/eclipse/mdmbl/org.eclipse.mdm.openatfx.mdf/"
    "-/archive/0.10/org.eclipse.mdm.openatfx.mdf-0.10.zip"
)

def run(cmd, cwd=None):
    print(">", " ".join(map(str, cmd)))
    subprocess.run(cmd, cwd=cwd, check=True)

def java_version():
    p = subprocess.run(["java", "-version"], capture_output=True, text=True)
    return p.returncode, (p.stderr or "") + (p.stdout or "")

def download(url, target):
    print("Downloading:", url)
    with urllib.request.urlopen(url) as r, open(target, "wb") as f:
        shutil.copyfileobj(r, f)

def main():
    code, ver = java_version()
    print(ver)
    if code != 0:
        raise SystemExit("Java was not found. Install a Java 8 JDK first.")

    if 'version "1.8' not in ver and "version '1.8" not in ver:
        print(
            "\nWARNING: the Eclipse 0.10 converter uses CORBA and is safest "
            "with Java 8. If the build fails, install/select a Java 8 JDK via JAVA_HOME.\n"
        )

    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    BUILD_ROOT.mkdir()
    TOOLS.mkdir(exist_ok=True)

    archive = BUILD_ROOT / "openatfx_mdf.zip"
    download(ECLIPSE_ARCHIVE, archive)

    with zipfile.ZipFile(archive) as z:
        z.extractall(BUILD_ROOT)

    candidates = [p for p in BUILD_ROOT.iterdir() if p.is_dir()]
    if not candidates:
        raise RuntimeError("Eclipse source archive did not contain a project directory.")
    project = candidates[0]

    build_gradle = project / "build.gradle"
    text = build_gradle.read_text(encoding="utf-8")

    patch = """
apply plugin: 'application'

mainClassName = 'org.eclipse.mdm.openatfx.mdf.ConvertMain'
"""
    if "mainClassName = 'org.eclipse.mdm.openatfx.mdf.ConvertMain'" not in text:
        build_gradle.write_text(text + "\n" + patch, encoding="utf-8")

    gradle = project / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if os.name != "nt":
        os.chmod(gradle, 0o755)

    run([str(gradle), "clean", "installDist"], cwd=project)

    installs = list((project / "build" / "install").glob("*"))
    if not installs:
        raise RuntimeError("Gradle installDist output was not found.")

    dest = TOOLS / "openatfx_mdf_converter"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(installs[0], dest)

    print("\nSetup complete.")
    print("Converter installed at:", dest)

if __name__ == "__main__":
    main()
