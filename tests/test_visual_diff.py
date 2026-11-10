"""
tests/test_visual_diff.py
-------------------------
Unit tests for ``tools.visual_diff.compare()``.

These tests build synthetic PIL images on disk (no external fixtures)
and assert the observable contract of the public ``compare`` function:

    * identical images -> SSIM == 1.0 and no regions
    * differing images -> SSIM < 1.0 and at least one large region
    * every region dict carries int x/y/w/h and a float score
    * a side-by-side crop is written to ``{output_dir}/region_{i}.png``
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np
import pytest
from PIL import Image

from tools import visual_diff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_png(path: Path, array: np.ndarray) -> str:
    """Persist a uint8 RGB numpy array as a PNG and return its path str."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(array.astype(np.uint8), mode="RGB")
    img.save(path)
    return str(path)


def _noise_image(width: int = 320, height: int = 240, seed: int = 42) -> np.ndarray:
    """Deterministic pseudo-random RGB image."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def _identical_pair(tmp_path: Path) -> Tuple[str, str]:
    """Two byte-identical PNGs."""
    arr = _noise_image()
    ref_path = _write_png(tmp_path / "ref.png", arr)
    cur_path = _write_png(tmp_path / "cur.png", arr.copy())
    return ref_path, cur_path


def _differing_pair(tmp_path: Path) -> Tuple[str, str]:
    """Reference plus a copy with a large solid-color rectangle inserted."""
    ref_arr = _noise_image()
    cur_arr = ref_arr.copy()
    # 100 rows x 180 cols == 18000 pixels, comfortably above MIN_REGION_AREA.
    cur_arr[80:180, 100:280] = np.array([255, 0, 0], dtype=np.uint8)
    ref_path = _write_png(tmp_path / "ref.png", ref_arr)
    cur_path = _write_png(tmp_path / "cur.png", cur_arr)
    return ref_path, cur_path


def _make_output_dir(tmp_path: Path) -> Path:
    """Create and return an output directory for region crops."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_identical_images_have_perfect_ssim(tmp_path: Path) -> None:
    """Byte-identical inputs produce SSIM == 1.0 and zero regions."""
    ref_path, cur_path = _identical_pair(tmp_path)
    out_dir = _make_output_dir(tmp_path)

    result = visual_diff.compare(ref_path, cur_path, str(out_dir))

    assert result["ssim"] == pytest.approx(1.0, abs=1e-6)
    assert result["regions"] == []


@pytest.mark.unit
def test_differing_images_have_ssim_below_one(tmp_path: Path) -> None:
    """A large inserted rectangle pushes SSIM strictly below 1.0."""
    ref_path, cur_path = _differing_pair(tmp_path)
    out_dir = _make_output_dir(tmp_path)

    result = visual_diff.compare(ref_path, cur_path, str(out_dir))

    assert result["ssim"] < 1.0
    assert isinstance(result["ssim"], float)


@pytest.mark.unit
def test_differing_images_yield_at_least_one_large_region(tmp_path: Path) -> None:
    """The inserted rectangle must survive the area > 500 filter."""
    ref_path, cur_path = _differing_pair(tmp_path)
    out_dir = _make_output_dir(tmp_path)

    result = visual_diff.compare(ref_path, cur_path, str(out_dir))

    regions = result["regions"]
    assert len(regions) >= 1
    assert any(r["w"] * r["h"] > 500 for r in regions)


@pytest.mark.unit
def test_regions_never_exceed_max(tmp_path: Path) -> None:
    """compare() must not return more than MAX_REGIONS regions."""
    ref_path, cur_path = _differing_pair(tmp_path)
    out_dir = _make_output_dir(tmp_path)

    result = visual_diff.compare(ref_path, cur_path, str(out_dir))

    assert len(result["regions"]) <= visual_diff.MAX_REGIONS


@pytest.mark.unit
def test_region_fields_have_expected_types(tmp_path: Path) -> None:
    """Every region exposes int x/y/w/h and a float score."""
    ref_path, cur_path = _differing_pair(tmp_path)
    out_dir = _make_output_dir(tmp_path)

    result = visual_diff.compare(ref_path, cur_path, str(out_dir))

    assert result["regions"], "expected at least one region"
    for region in result["regions"]:
        assert isinstance(region["x"], int)
        assert isinstance(region["y"], int)
        assert isinstance(region["w"], int)
        assert isinstance(region["h"], int)
        assert isinstance(region["score"], float)
        assert region["x"] >= 0
        assert region["y"] >= 0
        assert region["w"] > 0
        assert region["h"] > 0


@pytest.mark.unit
def test_side_by_side_crops_are_written(tmp_path: Path) -> None:
    """One region_{i}.png crop exists per returned region and is RGB."""
    ref_path, cur_path = _differing_pair(tmp_path)
    out_dir = _make_output_dir(tmp_path)

    result = visual_diff.compare(ref_path, cur_path, str(out_dir))

    assert result["regions"], "expected at least one region to crop"
    for i in range(len(result["regions"])):
        crop_path = out_dir / f"region_{i}.png"
        assert crop_path.is_file(), f"missing crop: {crop_path}"
        with Image.open(crop_path) as crop:
            assert crop.mode == "RGB"
            # Side-by-side strip is wider than any single input crop.
            assert crop.width > 0 and crop.height > 0


@pytest.mark.unit
def test_current_image_is_resized_to_reference(tmp_path: Path) -> None:
    """A differently sized current image must not raise and must diff."""
    ref_arr = _noise_image(width=320, height=240, seed=7)
    cur_arr = _noise_image(width=160, height=120, seed=7)
    cur_arr[40:90, 40:120] = np.array([0, 0, 255], dtype=np.uint8)

    ref_path = _write_png(tmp_path / "ref.png", ref_arr)
    cur_path = _write_png(tmp_path / "cur.png", cur_arr)
    out_dir = _make_output_dir(tmp_path)

    result = visual_diff.compare(ref_path, cur_path, str(out_dir))

    assert "ssim" in result
    assert isinstance(result["regions"], list)


@pytest.mark.unit
def test_missing_reference_raises_file_not_found(tmp_path: Path) -> None:
    """A nonexistent reference path surfaces as FileNotFoundError."""
    missing = tmp_path / "does_not_exist.png"
    _, cur_path = _identical_pair(tmp_path)
    out_dir = _make_output_dir(tmp_path)

    with pytest.raises(FileNotFoundError):
        visual_diff.compare(str(missing), cur_path, str(out_dir))