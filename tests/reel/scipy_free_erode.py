import numpy as np


def erode(mask: np.ndarray, r: int) -> np.ndarray:
    """Binary erosion with a (2r+1)^2 square, numpy only."""
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out &= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out
