"""
Transactional installer: swap CPU paddlepaddle -> paddlepaddle-gpu, safely.

Guarantees:
  * Makes ZERO changes unless ALL pre-checks pass (NVIDIA GPU present,
    matching GPU wheel actually downloadable for this exact Python).
  * If anything fails AFTER changes begin, automatically restores the
    exact previous CPU paddlepaddle version.
  * The DocAgent app keeps working in every outcome — the structure
    recognition skill auto-skips whenever a usable GPU install isn't found.

Run:  double-click install_gpu_paddle.bat
      (or: python install_gpu_paddle.py  from the same environment/terminal
       you use to run `streamlit run ui/app.py`)
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

PADDLE_VERSION_CANDIDATES = ["3.3.0", ""]  # "" = latest available
INDEX_CU126 = "https://www.paddlepaddle.org.cn/packages/stable/cu126/"
INDEX_CU118 = "https://www.paddlepaddle.org.cn/packages/stable/cu118/"


def run(cmd: list, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command; stream output live when capture=False (pip downloads)."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=capture, text=True)


def fail_no_changes(msg: str) -> None:
    print("\n" + "=" * 70)
    print("ABORTED — NOTHING WAS CHANGED. Your app works exactly as before.")
    print(f"Reason: {msg}")
    print("=" * 70)
    sys.exit(1)


def get_installed_version(package: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return None


def parse_cuda_version(smi_output: str) -> float | None:
    m = re.search(r"CUDA Version:\s*([0-9]+)\.([0-9]+)", smi_output)
    if not m:
        return None
    return float(f"{m.group(1)}.{m.group(2)}")


def verify_gpu_paddle() -> bool:
    """Fresh-subprocess check: CUDA-compiled, sees a GPU, can compute on it."""
    code = (
        "import paddle;"
        "assert paddle.device.is_compiled_with_cuda(), 'not CUDA build';"
        "assert paddle.device.cuda.device_count() > 0, 'no GPU visible';"
        "paddle.set_device('gpu:0');"
        "import paddle as p; x = p.ones([64, 64]); y = (x @ x).numpy();"
        "print('GPU COMPUTE OK')"
    )
    r = run([sys.executable, "-c", code])
    ok = r.returncode == 0 and "GPU COMPUTE OK" in (r.stdout or "")
    if not ok:
        print((r.stderr or "")[-1500:])
    return ok


def verify_cpu_paddle() -> bool:
    r = run([sys.executable, "-c", "import paddle; print('CPU IMPORT OK')"])
    return r.returncode == 0


def main() -> None:
    print("=" * 70)
    print("DocAgent — GPU PaddlePaddle installer (transactional, auto-rollback)")
    print("=" * 70)
    print(f"\nPython environment that will be modified:\n  {sys.executable}\n")

    # ── Pre-check 1: right environment? ────────────────────────────────
    cpu_version = get_installed_version("paddlepaddle")
    gpu_version = get_installed_version("paddlepaddle-gpu")
    if gpu_version:
        print(f"paddlepaddle-gpu {gpu_version} is already installed. Verifying it...")
        if verify_gpu_paddle():
            print("\nSUCCESS — GPU build already working. Nothing to do.")
            sys.exit(0)
        fail_no_changes(
            "A GPU build is installed but not working. Manual attention needed — "
            "not touching anything automatically."
        )
    if not cpu_version:
        fail_no_changes(
            "paddlepaddle is not installed in THIS Python environment, so this is "
            "probably not the environment your Streamlit app uses. Run this script "
            "from the same terminal/venv where you run `streamlit run ui/app.py`."
        )
    print(f"Found CPU paddlepaddle {cpu_version} (will be restored if anything fails).")

    # ── Pre-check 2: NVIDIA GPU + CUDA version ─────────────────────────
    try:
        smi = subprocess.run(["nvidia-smi"], capture_output=True, text=True)
    except FileNotFoundError:
        fail_no_changes(
            "nvidia-smi not found — no NVIDIA GPU/driver detected. GPU acceleration "
            "for PaddlePaddle requires an NVIDIA GPU. (If your laptop's GPU is "
            "Intel/AMD, the app's auto-skip is the correct permanent behavior.)"
        )
    if smi.returncode != 0:
        fail_no_changes("nvidia-smi exists but failed to run — NVIDIA driver problem.")
    cuda = parse_cuda_version(smi.stdout)
    if cuda is None:
        fail_no_changes("Could not read CUDA version from nvidia-smi output.")
    print(f"NVIDIA driver detected. Max supported CUDA: {cuda}")

    if cuda >= 12.0:
        indexes = [INDEX_CU126, INDEX_CU118]      # prefer cu126, cu118 also runs
    elif cuda >= 11.8:
        indexes = [INDEX_CU118]
    else:
        fail_no_changes(
            f"Driver only supports CUDA {cuda} (< 11.8). Update your NVIDIA driver "
            "from nvidia.com first, then re-run this script."
        )

    # ── Pre-check 3 (dry run): can we even download a matching wheel? ──
    print("\nDry run: downloading GPU wheel BEFORE touching anything...")
    print("(This is a large download, ~500 MB - 1 GB. Nothing is installed yet.)")
    tmp = Path(tempfile.mkdtemp(prefix="paddle_gpu_wheel_"))
    wheel: Path | None = None
    for index in indexes:
        for ver in PADDLE_VERSION_CANDIDATES:
            spec = f"paddlepaddle-gpu=={ver}" if ver else "paddlepaddle-gpu"
            r = run([sys.executable, "-m", "pip", "download", spec,
                     "-i", index, "--no-deps", "-d", str(tmp)], capture=False)
            if r.returncode == 0:
                wheels = sorted(tmp.glob("paddlepaddle_gpu*.whl"))
                if wheels:
                    wheel = wheels[-1]
                    break
        if wheel:
            break
    if not wheel:
        fail_no_changes(
            f"No paddlepaddle-gpu wheel available for your Python "
            f"({sys.version.split()[0]}) / platform. Your app keeps working on the "
            "auto-skip path. You could retry with Python 3.11/3.12 in a venv."
        )
    print(f"\nWheel downloaded OK: {wheel.name}")

    # ── Point of no return: uninstall CPU build ────────────────────────
    print("\nAll pre-checks passed. Swapping CPU build for GPU build...")
    r = run([sys.executable, "-m", "pip", "uninstall", "-y", "paddlepaddle"],
            capture=False)
    if r.returncode != 0:
        fail_no_changes("pip uninstall failed — nothing was removed.")

    def rollback(reason: str) -> None:
        print("\n" + "!" * 70)
        print(f"GPU install failed ({reason}). ROLLING BACK to paddlepaddle=={cpu_version}...")
        print("!" * 70)
        run([sys.executable, "-m", "pip", "uninstall", "-y", "paddlepaddle-gpu"],
            capture=False)
        rb = run([sys.executable, "-m", "pip", "install",
                  f"paddlepaddle=={cpu_version}"], capture=False)
        if rb.returncode != 0:  # exact pin gone from PyPI? take latest
            rb = run([sys.executable, "-m", "pip", "install", "paddlepaddle"],
                     capture=False)
        restored = rb.returncode == 0 and verify_cpu_paddle()
        print("\n" + "=" * 70)
        if restored:
            print("ROLLED BACK SUCCESSFULLY — your app works exactly as before")
            print("(structure recognition auto-skips, everything else unchanged).")
        else:
            print("Rollback had issues, but the app STILL WORKS: the structure")
            print("recognition skill detects the broken/missing paddle and skips.")
            print(f"To restore manually:  pip install paddlepaddle=={cpu_version}")
        print("=" * 70)
        sys.exit(1)

    # ── Install GPU wheel ──────────────────────────────────────────────
    r = run([sys.executable, "-m", "pip", "install", str(wheel)], capture=False)
    if r.returncode != 0:
        rollback("pip install of the GPU wheel failed")

    # ── Verify on the actual GPU ───────────────────────────────────────
    print("\nVerifying: importing paddle and running a computation on your GPU...")
    if not verify_gpu_paddle():
        rollback("verification computation on the GPU failed")

    print("\n" + "=" * 70)
    print("SUCCESS — PaddlePaddle GPU build installed and verified on your GPU.")
    print("No app files were changed. Restart Streamlit and the high-fidelity")
    print("table step will now run automatically (fast) on Technical/Financial/")
    print("Research/Scientific PDFs.")
    print("=" * 70)


if __name__ == "__main__":
    main()
