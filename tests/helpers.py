"""
Synthetic JPEG frame factories for liveness and quality tests.

All frames are 320×240 pixels.  Each factory returns raw JPEG bytes
that are valid inputs to both cv2.imdecode and PIL.Image.open.

Frame types
-----------
sharp_frame()       Checkerboard with 20 px blocks — high Laplacian variance.
noisy_frame(seed)   sharp_frame + uniform pixel noise ±12 — simulates micro-motion.
blurry_frame()      Heavily Gaussian-blurred — Laplacian variance << 50.
high_motion_frame() Phase-shifted checkerboard — ~50 % pixels differ by ≈160 units.
"""

import io

import cv2
import numpy as np
from PIL import Image

_W, _H, _BLOCK = 320, 240, 20
_BRIGHT, _DARK = 200, 40


def _checkerboard(x_offset: int = 0, y_offset: int = 0) -> np.ndarray:
    xs = np.arange(_W, dtype=np.int32)
    ys = np.arange(_H, dtype=np.int32)
    xx, yy = np.meshgrid(xs, ys)
    return np.where(
        ((xx + x_offset) // _BLOCK + (yy + y_offset) // _BLOCK) % 2 == 0,
        _BRIGHT,
        _DARK,
    ).astype(np.uint8)


def _encode(arr: np.ndarray, quality: int = 92) -> bytes:
    """Encode a 2-D (greyscale) or 3-D (RGB) uint8 array to JPEG bytes."""
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def sharp_frame() -> bytes:
    """
    Clean checkerboard.
    Laplacian variance >> 50 ✓   Mean diff against itself == 0.
    """
    return _encode(_checkerboard())


def noisy_frame(seed: int = 0) -> bytes:
    """
    Checkerboard + uniform noise in [−12, +12].
    Laplacian variance >> 50 ✓   Mean diff vs sharp_frame ≈ 4–8
    (well inside the liveness pass band 0.15–15.0) ✓
    """
    rng = np.random.default_rng(seed)
    base = _checkerboard().astype(np.int16)
    noise = rng.integers(-12, 13, size=base.shape, dtype=np.int16)
    return _encode(np.clip(base + noise, 0, 255).astype(np.uint8))


def blurry_frame() -> bytes:
    """
    Heavily Gaussian-blurred checkerboard (kernel 61×61, σ ≈ 9.5).
    Laplacian variance << 50 → fails the per-frame texture check ✓
    """
    blurred = cv2.GaussianBlur(_checkerboard(), (61, 61), 0)
    return _encode(blurred)


def high_motion_frame() -> bytes:
    """
    Checkerboard phase-shifted by half a block (10 px).
    ≈ 50 % of pixels differ by ≈ 160 units vs sharp_frame.
    Mean ROI diff >> 15.0 → fails the excessive-motion guard ✓
    Both frames are individually sharp (Laplacian variance >> 50) ✓
    """
    return _encode(_checkerboard(x_offset=10, y_offset=10))
