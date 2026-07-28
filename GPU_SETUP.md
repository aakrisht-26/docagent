# Enabling GPU acceleration for high-fidelity tables (PP-Structure)

## What was wrong

The `structure_recognition` skill runs PaddleOCR **PP-Structure** — a heavy
deep-learning model — on every page of Technical/Financial/Research/Scientific
PDFs. On a **GPU** it takes a few seconds per page. On a **CPU** it takes
**15–25 minutes per page**, so a large document looks completely frozen. That is
why the app "never processed through" on your Windows machine.

Your `requirements.txt` installs **`paddlepaddle`**, which is the **CPU-only**
build. It will not use your GPU even though your laptop has one — the GPU build
(`paddlepaddle-gpu`) is a separate package.

## What was fixed in code

`skills/structure_recognition_skill.py` now checks whether PaddlePaddle can
actually see a CUDA GPU:

- **No usable GPU** → it skips the heavy step instantly and the pipeline
  continues (basic table extraction from the parser still works). The app is now
  usable immediately, even before you do anything below.
- **Usable GPU present** → it runs PP-Structure with GPU acceleration, as
  intended.

So the app works right now. Follow the steps below only if you want the
high-fidelity table extraction running fast on your GPU.

## How to enable the GPU (NVIDIA only)

> This requires an **NVIDIA** GPU + driver. If your laptop's GPU is Intel/AMD,
> GPU acceleration is not available and the auto-skip above is the right
> permanent behavior.

**1. Check your CUDA version** (look at the top-right "CUDA Version" figure):

```
nvidia-smi
```

**2. Remove the CPU build:**

```
pip uninstall paddlepaddle paddlepaddle-gpu -y
```

**3. Install the GPU build matching your CUDA** (from PaddlePaddle's official index):

```
# CUDA 11.8
python -m pip install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/

# CUDA 12.6 (use this for CUDA 12.x)
python -m pip install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
```

The wheel bundles the CUDA/cuDNN runtime, so you don't have to install those
separately — you only need a recent NVIDIA driver. Pick the index (`cu118` vs
`cu126`) closest to, but not higher than, the CUDA version `nvidia-smi` reports.

**4. Verify PaddlePaddle sees the GPU:**

```
python -c "import paddle; print('CUDA compiled:', paddle.device.is_compiled_with_cuda(), '| GPUs:', paddle.device.cuda.device_count())"
```

You want `CUDA compiled: True | GPUs: 1` (or more). A fuller check:

```
python -c "import paddle; paddle.utils.run_check()"
```

Once that reports a GPU, no code change is needed — the skill detects it and
starts using it automatically on the next run.

## If you'd rather keep it CPU (not recommended)

If you ever want PP-Structure to run on CPU anyway (accepting the very slow
speed), set this in `configs/default.yaml`:

```yaml
pdf:
  allow_cpu_structure: true
```

## Reference

- PaddlePaddle install guide: https://www.paddlepaddle.org.cn/en/install/quick
