# tools/plugin_manager.py
import os
import shutil
import tempfile
import zipfile

try:
    import yaml
    HAS_YAML = True
except ImportError:
    yaml = None
    HAS_YAML = False

PLUGIN_EXTENSION = ".differixplugin"
MANIFEST_FILENAME = "plugin.yaml"

ALLOWED_EXTENSIONS = {".md", ".json", ".yaml", ".css", ".html", ".glsl", ".txt"}

REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "author", "description", "skills", "lessons")


class PluginManager:
    def __init__(self, project_dir: str):
        self.project_dir = os.path.abspath(project_dir)
        self.plugins_root = os.path.join(self.project_dir, ".differix", "plugins")

    # ------------------------------------------------------------------ #
    # Install
    # ------------------------------------------------------------------ #
    def install(self, plugin_path: str) -> tuple:
        """Install a .differixplugin archive. Returns (True, "Success") or (False, reason)."""
        plugin_path = os.path.abspath(plugin_path)
        if not os.path.isfile(plugin_path):
            return False, f"Plugin file not found: {plugin_path}"
        if not plugin_path.endswith(PLUGIN_EXTENSION):
            return False, f"Invalid plugin file extension. Expected '{PLUGIN_EXTENSION}'"
        if not HAS_YAML:
            return False, "PyYAML is required to install plugins (pip install pyyaml)"

        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="differix_plugin_")
            ok, reason = self._extract_archive(plugin_path, temp_dir)
            if not ok:
                return False, reason

            manifest_path = self._locate_manifest(temp_dir)
            if manifest_path is None:
                return False, f"Plugin manifest '{MANIFEST_FILENAME}' not found in archive"

            ok, reason = self._scan_extensions(temp_dir)
            if not ok:
                return False, reason

            ok, reason, manifest = self._parse_manifest(manifest_path)
            if not ok:
                return False, reason

            plugin_id = str(manifest["id"])
            scan_root = os.path.dirname(manifest_path)

            # Move valid plugin into plugins root
            dest = os.path.join(self.plugins_root, plugin_id)
            if os.path.exists(dest):
                return False, f"Plugin '{plugin_id}' is already installed. Remove it first."
            os.makedirs(self.plugins_root, exist_ok=True)
            shutil.move(scan_root, dest)
            return True, "Success"
        except Exception as e:
            return False, f"Install failed: {e}"
        finally:
            if temp_dir is not None and os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Helpers: extraction, scanning, manifest parsing
    # ------------------------------------------------------------------ #
    def _extract_archive(self, plugin_path: str, temp_dir: str) -> tuple:
        """Safely extract a plugin ZIP into temp_dir, guarding against path traversal."""
        try:
            with zipfile.ZipFile(plugin_path, "r") as zf:
                for name in zf.namelist():
                    if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                        return False, f"Security check failed: unsafe path in archive: {name}"
                zf.extractall(temp_dir)
        except zipfile.BadZipFile:
            return False, "Invalid plugin archive: not a valid ZIP file"
        return True, ""

    def _locate_manifest(self, temp_dir: str):
        """Find plugin.yaml, supporting archives with or without a top-level folder."""
        candidate = os.path.join(temp_dir, MANIFEST_FILENAME)
        if os.path.isfile(candidate):
            return candidate
        for root, dirs, files in os.walk(temp_dir):
            if MANIFEST_FILENAME in files:
                return os.path.join(root, MANIFEST_FILENAME)
        return None

    def _scan_extensions(self, temp_dir: str) -> tuple:
        """Reject any file whose extension is not in the allowlist."""
        for root, dirs, files in os.walk(temp_dir):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ALLOWED_EXTENSIONS:
                    return False, (
                        f"Security check failed: executable or disallowed file "
                        f"type '{ext or fname}' found: {fname}. Allowed: "
                        f"{', '.join(sorted(ALLOWED_EXTENSIONS))}"
                    )
        return True, ""

    def _parse_manifest(self, manifest_path: str) -> tuple:
        """Parse and validate plugin.yaml. Returns (ok, reason, manifest)."""
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = yaml.safe_load(f)
        except Exception as e:
            return False, f"Failed to parse {MANIFEST_FILENAME}: {e}", None
        if not isinstance(manifest, dict):
            return False, f"Invalid {MANIFEST_FILENAME}: expected a mapping at top level", None

        missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in manifest or manifest[f] in (None, "")]
        if missing:
            return False, f"Invalid {MANIFEST_FILENAME}: missing required fields: {', '.join(missing)}", None

        plugin_id = str(manifest["id"])
        if not plugin_id.replace("_", "").replace("-", "").isalnum():
            return False, f"Invalid plugin id: {plugin_id!r} (letters, digits, '-', '_' only)", None
        for key in ("skills", "lessons"):
            if not isinstance(manifest[key], list):
                return False, f"Invalid {MANIFEST_FILENAME}: field '{key}' must be a list", None
        return True, "", manifest

    # ------------------------------------------------------------------ #
    # List / Remove
    # ------------------------------------------------------------------ #
    def list_plugins(self) -> list:
        """Return the manifests of all installed plugins."""
        plugins = []
        if not os.path.isdir(self.plugins_root):
            return plugins
        for entry in sorted(os.listdir(self.plugins_root)):
            manifest_path = os.path.join(self.plugins_root, entry, MANIFEST_FILENAME)
            if not os.path.isfile(manifest_path):
                continue
            ok, _reason, manifest = self._parse_manifest(manifest_path)
            if ok and manifest:
                plugins.append(manifest)
        return plugins

    def _plugin_dir(self, plugin_id: str):
        """Return the install directory for a plugin id, or None if the id is unsafe."""
        if not plugin_id or not plugin_id.replace("_", "").replace("-", "").isalnum():
            return None
        return os.path.join(self.plugins_root, plugin_id)

    def remove(self, plugin_id: str) -> bool:
        """Delete an installed plugin directory. Returns True on success."""
        plugin_dir = self._plugin_dir(plugin_id)
        if plugin_dir is None:
            return False
        if not os.path.isdir(plugin_dir):
            return False
        shutil.rmtree(plugin_dir, ignore_errors=True)
        return os.path.isdir(plugin_dir) is False

    def is_installed(self, plugin_id: str) -> bool:
        """Return True if a plugin with the given id is installed."""
        plugin_dir = self._plugin_dir(plugin_id)
        return plugin_dir is not None and os.path.isdir(plugin_dir)

    def get_plugin_path(self, plugin_id: str):
        """Return the install directory of a plugin, or None if not installed."""
        plugin_dir = self._plugin_dir(plugin_id)
        if plugin_dir is not None and os.path.isdir(plugin_dir):
            return plugin_dir
        return None

    # ------------------------------------------------------------------ #
    # Canonical helper aliases (final part 1/2)
    # ------------------------------------------------------------------ #
    def _safe_extract(self, plugin_path: str, temp_dir: str) -> tuple:
        """Traversal-safe extraction of a plugin archive into temp_dir."""
        return self._extract_archive(plugin_path, temp_dir)

    def _validate_manifest(self, manifest_path: str) -> tuple:
        """Parse and validate a manifest file. Returns (ok, reason, manifest)."""
        return self._parse_manifest(manifest_path)

    # ------------------------------------------------------------------ #
    # Canonical helper aliases (final part 2/2)
    # ------------------------------------------------------------------ #
    def _extract_safely(self, plugin_path: str, temp_dir: str) -> tuple:
        """Alias for traversal-safe archive extraction."""
        return self._extract_archive(plugin_path, temp_dir)

    def _scan_for_executables(self, scan_dir: str) -> tuple:
        """Alias for the extension allowlist security scan."""
        return self._scan_extensions(scan_dir)

    def _parse_plugin_manifest(self, manifest_path: str) -> tuple:
        """Alias for manifest parsing and validation."""
        return self._parse_manifest(manifest_path)