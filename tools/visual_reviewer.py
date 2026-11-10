import os
from typing import Optional, Dict
from pathlib import Path

def review_page(html_path: str, reference_png: Optional[str], output_dir: str) -> Dict:
    """Review an HTML page by comparing it visually to a reference image.
    
    Args:
        html_path: Path to HTML file to review
        reference_png: Optional path to reference PNG for comparison
        output_dir: Directory to save diff results and artifacts
        
    Returns:
        Dict with review results containing:
        - ok: bool - True if visual match passes threshold or no reference
        - ssim: Optional[float] - Structural similarity score (0-1)
        - regions: Optional[list] - Diff regions if ssim < 0.85
        - feedback: str - Human-readable summary of results
    """
    from tools.visual_capture import capture_page
    from tools.visual_diff import compare
    
    # Ensure output dir exists
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Capture current page
    try:
        captured_png = os.path.join(output_dir, "current.png")
        capture_page(html_path, captured_png, viewport=(1440, 900))
    except Exception as e:
        return {
            "ok": False,
            "ssim": None,
            "feedback": f"Failed to capture page: {str(e)}"
        }
    
    # Handle missing reference case
    if not reference_png or not os.path.exists(reference_png):
        return {
            "ok": True, 
            "ssim": None,
            "feedback": "no reference"
        }
    
    # Compare to reference
    try:
        diff_result = compare(reference_png, captured_png, output_dir)
        ssim = diff_result.get("ssim", 0)
        
        if ssim >= 0.85:
            return {
                "ok": True,
                "ssim": ssim,
                "feedback": ""
            }
            
        # Generate feedback for failed diff
        regions = diff_result.get("regions", [])
        top_regions = sorted(regions, key=lambda r: r["score"])[:3]
        
        feedback_lines = ["Visual differences detected:"]
        for i, region in enumerate(top_regions, 1):
            x, y = region["x"], region["y"]
            score = region["score"]
            feedback_lines.append(f"{i}. Diff at region {x},{y} (score {score:.2f})")
            
        return {
            "ok": False,
            "ssim": ssim,
            "regions": regions,
            "feedback": "\n".join(feedback_lines)
        }
        
    except Exception as e:
        return {
            "ok": False,
            "ssim": None,
            "feedback": f"Diff failed: {str(e)}"
        }