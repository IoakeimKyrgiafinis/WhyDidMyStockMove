from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]


def benjamini_hochberg(p_values: FloatArray, q: float = 0.05) -> tuple[FloatArray, BoolArray]:
    """Benjamini-Hochberg false discovery rate control.

    Returns (adjusted p-values, rejected). A hypothesis is rejected when its adjusted
    p-value is at most q, which keeps the expected share of false discoveries among the
    rejections at or below q (for independent or positively dependent tests).
    """
    p = np.asarray(p_values, dtype=np.float64)
    if p.ndim != 1:
        raise ValueError("p_values must be one-dimensional")
    if not 0.0 < q < 1.0:
        raise ValueError("q must be between 0 and 1")
    if len(p) == 0:
        return p.copy(), np.zeros(0, dtype=np.bool_)
    if not np.isfinite(p).all():
        raise ValueError("p-values must be finite")
    if bool(((p < 0.0) | (p > 1.0)).any()):
        raise ValueError("p-values must lie between 0 and 1")

    m = len(p)
    order = np.argsort(p, kind="mergesort")
    scaled = p[order] * m / np.arange(1, m + 1)
    step_up = np.minimum.accumulate(scaled[::-1])[::-1]

    adjusted = np.empty(m, dtype=np.float64)
    adjusted[order] = np.minimum(step_up, 1.0)
    return adjusted, adjusted <= q
