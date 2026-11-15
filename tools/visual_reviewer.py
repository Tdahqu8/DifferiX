import os
from typing import Optional, Dict
from pathlib import Path

def review_screenshot(
    current_png: str,
    reference_png: Optional[str],
    output_dir: str
) -> Dict:
    """Сравнивает два уже готовых скриншота (от Playwright или Godot MCP)."""
    from tools.visual_diff import compare
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    if not reference_png or not os.path.exists(reference_png):
        return {"ok": True, "ssim": None, "feedback": "no reference"}
    
    try:
        diff_result = compare(reference_png, current_png, output_dir)
        ssim = diff_result.get("ssim", 0)
        
        if ssim >= 0.85:
            return {"ok": True, "ssim": ssim, "feedback": ""}
            
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
        return {"ok": False, "ssim": None, "feedback": f"Diff failed: {str(e)}"}


def review_page(html_path: str, reference_png: Optional[str], output_dir: str) -> Dict:
    """(Для веба) Делает скриншот через Playwright и сравнивает."""
    from tools.visual_capture import capture_page
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    try:
        captured_png = os.path.join(output_dir, "current.png")
        capture_page(html_path, captured_png, viewport=(1440, 900))
    except Exception as e:
        return {"ok": False, "ssim": None, "feedback": f"Failed to capture page: {str(e)}"}
        
    return review_screenshot(captured_png, reference_png, output_dir)