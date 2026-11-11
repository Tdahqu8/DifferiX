"""
tools/visual_diff.py
--------------------
Visual regression diffing pipeline for the Differix web-dev plugin.

Given a reference screenshot and a freshly captured screenshot, this
module computes a structural-similarity (SSIM) score, locates the
regions where the two images diverge, and writes side-by-side crops of
each divergent region for human inspection.

Public API:
    compare(reference_png, current_png, output_dir) -> dict

Pipeline:
    1. Load both PNGs with PIL and force RGB.
    2. Resize the current image to match the reference dimensions.
    3. Convert to grayscale with skimage.color.rgb2gray.
    4. Compute SSIM (full=True) with skimage.metrics.structural_similarity.
    5. Binarize the diff map: (1 - ssim_map) > 0.35.
    6. Dilate the mask with scipy.ndimage.binary_dilation(iterations=8).
    7. Label connected components and extract regionprops.
    8. Sort regions by area, keep the top 5 with area > 500.
    9. For each kept region, crop the padded bounding box from both
       images and save them side-by-side to {output_dir}/region_{i}.png.
   10. Return {"ssim": float, "regions": [...]}.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List

import numpy as np
from PIL import Image

from scipy import ndimage

from skimage import color
from skimage.metrics import structural_similarity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunable constants (kept module-level so callers/tests can introspect them)
# ---------------------------------------------------------------------------
DIFF_THRESHOLD: float = 0.35
DILATION_ITERATIONS: int = 8
MAX_REGIONS: int = 5
MIN_REGION_AREA: int = 500
CROP_PADDING: int = 12


def _load_rgb(path: str) -> Image.Image:
    """Load an image from disk and force it into RGB mode."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Image not found: {path}")
    with Image.open(path) as img:
        return img.convert("RGB")


def _pad_bbox(
    x: int,
    y: int,
    w: int,
    h: int,
    width: int,
    height: int,
    padding: int = CROP_PADDING,
) -> tuple:
    """Clamp a padded bounding box to the image bounds."""
    x0 = max(0, int(x) - padding)
    y0 = max(0, int(y) - padding)
    x1 = min(width, int(x + w) + padding)
    y1 = min(height, int(y + h) + padding)
    return x0, y0, x1, y1


def _side_by_side(reference_crop: Image.Image, current_crop: Image.Image) -> Image.Image:
    """Combine two crops into a single horizontal strip with a divider."""
    ref_w, ref_h = reference_crop.size
    cur_w, cur_h = current_crop.size
    height = max(ref_h, cur_h)
    gap = 8

    canvas = Image.new("RGB", (ref_w + gap + cur_w, height), (0, 0, 0))
    canvas.paste(reference_crop, (0, 0))
    canvas.paste(current_crop, (ref_w + gap, 0))
    return canvas


def compare(reference_png: str, current_png: str, output_dir: str) -> Dict:
    """
    Compare two PNG screenshots and report the divergent regions.

    Parameters
    ----------
    reference_png : str
        Path to the baseline (reference) screenshot.
    current_png : str
        Path to the freshly captured screenshot.
    output_dir : str
        Directory where per-region side-by-side crops are written.

    Returns
    -------
    dict
        {
            "ssim": float,
            "regions": [
                {"x": int, "y": int, "w": int, "h": int, "score": float},
                ...
            ],
        }
    """
    reference_img = _load_rgb(reference_png)
    current_img = _load_rgb(current_png)

    # Resize the current image to match the reference dimensions.
    if current_img.size != reference_img.size:
        logger.debug(
            "Resizing current image %s -> %s to match reference",
            current_img.size,
            reference_img.size,
        )
        current_img = current_img.resize(reference_img.size, Image.LANCZOS)

    ref_array = np.asarray(reference_img, dtype=np.float64)
    cur_array = np.asarray(current_img, dtype=np.float64)

    # Convert to grayscale (skimage expects float in [0, 1]).
    ref_gray = color.rgb2gray(ref_array / 255.0)
    cur_gray = color.rgb2gray(cur_array / 255.0)

    # Structural similarity — full=True returns the per-pixel SSIM map.
    ssim_score, ssim_map = structural_similarity(
        ref_gray,
        cur_gray,
        full=True,
        data_range=1.0,
    )

    # Binarize the diff map: where the images disagree strongly.
    diff_map = 1.0 - ssim_map
    binary_mask = diff_map > DIFF_THRESHOLD

    # Merge nearby pixels into coherent blobs.
    dilated_mask = ndimage.binary_dilation(
        binary_mask,
        iterations=DILATION_ITERATIONS,
    )

    # Label connected components.
    labeled, num_labels = ndimage.label(dilated_mask)

    height, width = ref_gray.shape
    regions: List[Dict] = []

    if num_labels > 0:
        props = ndimage.find_objects(labeled)
        candidates: List[Dict] = []
        for label_index, slice_tuple in enumerate(props, start=1):
            if slice_tuple is None:
                continue
            ys, xs = slice_tuple
            y0, y1 = int(ys.start), int(ys.stop)
            x0, x1 = int(xs.start), int(xs.stop)
            w = x1 - x0
            h = y1 - y0
            area = int(np.count_nonzero(labeled[slice_tuple] == label_index))
            if area <= MIN_REGION_AREA:
                continue
            region_diff = diff_map[slice_tuple]
            score = float(np.mean(region_diff)) if region_diff.size else 0.0
            candidates.append(
                {
                    "x": x0,
                    "y": y0,
                    "w": w,
                    "h": h,
                    "area": area,
                    "score": score,
                }
            )

        candidates.sort(key=lambda r: r["area"], reverse=True)
        candidates = candidates[:MAX_REGIONS]

        os.makedirs(output_dir, exist_ok=True)

        for index, region in enumerate(candidates):
            px0, py0, px1, py1 = _pad_bbox(
                region["x"],
                region["y"],
                region["w"],
                region["h"],
                width,
                height,
            )
            ref_crop = reference_img.crop((px0, py0, px1, py1))
            cur_crop = current_img.crop((px0, py0, px1, py1))
            composite = _side_by_side(ref_crop, cur_crop)
            out_path = os.path.join(output_dir, f"region_{index}.png")
            composite.save(out_path)
            logger.debug("Wrote diff region crop: %s", out_path)

            regions.append(
                {
                    "x": region["x"],
                    "y": region["y"],
                    "w": region["w"],
                    "h": region["h"],
                    "score": region["score"],
                }
            )
    else:
        os.makedirs(output_dir, exist_ok=True)

    return {"ssim": float(ssim_score), "regions": regions}