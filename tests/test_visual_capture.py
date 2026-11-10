import os
import pytest
from pathlib import Path
from tools.visual_capture import capture_page

@pytest.mark.integration
def test_capture_page(tmp_path):
    """Integration test for visual capture functionality."""
    # Skip if playwright is not installed
    pytest.importorskip("playwright")

    # Create a dummy HTML file
    html_path = tmp_path / "test.html"
    html_path.write_text("<html><body><h1>Test</h1></body></html>")

    # Capture the page
    output_dir = tmp_path / "screenshots"
    screenshot_path = capture_page(str(html_path), str(output_dir))

    # Verify results
    assert os.path.isabs(screenshot_path), "Path should be absolute"
    assert os.path.exists(screenshot_path), "Screenshot file should exist"
    assert os.path.getsize(screenshot_path) > 0, "Screenshot file should not be empty"