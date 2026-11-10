import os
import re
import httpx
from ipaddress import ip_address
from typing import Dict, List, Optional
from urllib.parse import urlparse

class AssetManager:
    """Manages downloading and indexing web assets with security checks."""
    
    EXTENSION_MAP = {
        "image": [".png", ".jpg", ".jpeg", ".webp", ".svg"],
        "shader": [".glsl", ".frag", ".vert"],
        "html": [".html"],
        "3d": [".glb", ".gltf"]
    }
    
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    
    def __init__(self, project_dir: str):
        self.project_dir = os.path.abspath(project_dir)
        self.assets_dir = os.path.join(self.project_dir, ".differix", "assets")
        os.makedirs(self.assets_dir, exist_ok=True)
        
    def _validate_extension(self, filename: str, asset_type: str) -> bool:
        """Check if file extension matches allowed types for the asset type."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in self.EXTENSION_MAP.get(asset_type, [])
    
    def _is_private_ip(self, host: str) -> bool:
        """Check if host resolves to a private IP address."""
        try:
            ip = ip_address(host)
            return ip.is_private or ip.is_loopback
        except ValueError:
            return False
    
    def _validate_url(self, url: str) -> bool:
        """Validate URL security - reject private IPs and invalid schemes."""
        parsed = urlparse(url)
        if parsed.scheme not in ["http", "https"]:
            return False
        if self._is_private_ip(parsed.hostname):
            return False
        return True
    
    def download_asset(self, url: str, asset_type: str, filename: str) -> str:
        """Download an asset with security checks and save to assets directory.
        
        Args:
            url: Source URL to download from (must pass SSRF checks)
            asset_type: Type of asset (image/shader/html/3d)
            filename: Target filename (must have correct extension for type)
            
        Returns:
            Absolute path to saved asset
            
        Raises:
            ValueError: For security violations, invalid types, or download failures
        """
        if not self._validate_url(url):
            raise ValueError("Invalid or insecure URL")
            
        if not self._validate_extension(filename, asset_type):
            raise ValueError(f"Invalid extension for asset type '{asset_type}'")
            
        # Sanitize filename to prevent path traversal
        filename = os.path.basename(filename)
        if not filename or filename.startswith('.'):
            raise ValueError("Invalid filename")
            
        target_dir = os.path.join(self.assets_dir, asset_type)
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, filename)
        
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            try:
                with client.stream("GET", url) as response:
                    # Recheck SSRF after following redirects
                    final_url = str(response.url)
                    parsed = urlparse(final_url)
                    if self._is_private_ip(parsed.hostname):
                        raise ValueError("Redirect leads to private IP - SSRF blocked")
                        
                    if response.status_code != 200:
                        raise ValueError(f"Failed to download asset: HTTP {response.status_code}")
                        
                    # Stream download with size cap
                    total_bytes = 0
                    chunks = []
                    for chunk in response.iter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.MAX_FILE_SIZE:
                            raise ValueError(f"File exceeds {self.MAX_FILE_SIZE//(1024*1024)}MB limit")
                        chunks.append(chunk)
                        
                    content = b''.join(chunks)
                    
                    # Verify content type matches asset type
                    content_type = response.headers.get('content-type', '')
                    if asset_type == 'image' and not content_type.startswith('image/'):
                        raise ValueError(f"Expected image content type, got {content_type}")
                    elif asset_type == '3d' and not any(x in content_type for x in ['model/gltf', 'model/gltf-binary']):
                        raise ValueError(f"Expected 3D model content type, got {content_type}")
                        
                    # Write file atomically
                    temp_path = target_path + '.tmp'
                    with open(temp_path, 'wb') as f:
                        f.write(content)
                    os.replace(temp_path, target_path)
                    
            except httpx.RequestError as e:
                raise ValueError(f"Download failed: {str(e)}")
                
        return os.path.abspath(target_path)