import os
from pathlib import Path
from typing import Optional
from memory.schemas import MemoryStorageError

def capture_page(url_or_path: str, output_dir: str, viewport: tuple = (1440, 900)) -> str:
    """Capture a full-page screenshot of a webpage or local HTML file.
    
    Args:
        url_or_path: URL or path to HTML file
        output_dir: Directory to save screenshot
        viewport: Viewport size (width, height)
        
    Returns:
        Absolute path to saved PNG file
        
    Raises:
        MemoryStorageError: If page loading fails
    """
    try:
        from playwright.sync_api import sync_playwright
        
        # Ensure output directory exists
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Set viewport size
            page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
            
            # Inject styles to pause animations
            page.add_style_tag(content="""
                *, *::before, *::after {
                    animation-play-state: paused !important;
                    transition: none !important;
                }
            """)
            
            # Load the page
            if os.path.exists(url_or_path):
                page.goto(f"file://{os.path.abspath(url_or_path)}")
            else:
                page.goto(url_or_path)
                
            # Wait for fonts to load and add small delay
            page.evaluate("""async () => {
                await document.fonts.ready;
                await new Promise(resolve => setTimeout(resolve, 500));
            }""")
            
            # Take screenshot
            output_path = os.path.join(
                output_dir,
                f"{viewport[0]}x{viewport[1]}.png"
            )
            page.screenshot(path=output_path, full_page=True)
            
            browser.close()
            
            return os.path.abspath(output_path)
            
    except Exception as e:
        raise MemoryStorageError(f"Failed to capture page: {str(e)}")