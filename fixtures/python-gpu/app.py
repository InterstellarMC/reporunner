import shutil
def check():
    if not shutil.which("nvidia-smi"):
        raise RuntimeError("CUDA unavailable: GPU hardware required")
    return True
