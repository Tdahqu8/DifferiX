import os
import stat

# Prefer O_NOFOLLOW where available (Linux/BSD); fall back to 0 on platforms
# lacking it, where the fstat/lstat verification below still catches symlinks.
_O_NOFOLLOW = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0


class FolderSandbox:

    def __init__(self, root_dir: str):
        self.root_dir = os.path.realpath(os.path.expanduser(root_dir))

    def _get_absolute_path(self, target_path: str) -> str:
        expanded = os.path.expanduser(target_path)
        if os.path.isabs(expanded):
            return os.path.realpath(expanded)
        return os.path.realpath(os.path.join(self.root_dir, expanded))

    def _is_safe_path(self, abs_target: str) -> bool:
        """Validates that target file stays inside active project directory anywhere on disk."""
        try:
            return os.path.commonpath([self.root_dir, abs_target]) == self.root_dir
        except ValueError:
            # Handles cross-drive path checks on Windows/mounted volumes
            return False

    def _open_sandboxed(self, abs_path: str, write: bool):
        """
        Open a file inside the sandbox without following symlinks.

        Hardens against TOCTOU symlink swaps: the final component is opened
        with O_NOFOLLOW, the opened descriptor is verified to be a regular
        file, and the on-disk path is re-checked against the descriptor to
        detect a symlink swapped in between resolution and open.
        """
        if not self._is_safe_path(abs_path):
            raise PermissionError(
                f"Path '{abs_path}' is outside active workspace root '{self.root_dir}'."
            )

        if write:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        else:
            flags = os.O_RDONLY
        flags |= _O_NOFOLLOW

        try:
            fd = os.open(abs_path, flags, 0o600)
        except OSError as e:
            raise PermissionError(f"Cannot open '{abs_path}': {e}")

        try:
            st_fd = os.fstat(fd)
            if not stat.S_ISREG(st_fd.st_mode):
                raise PermissionError(
                    f"Refusing non-regular file '{abs_path}'."
                )
            # Detect a symlink swapped in at the final component after open.
            try:
                st_path = os.lstat(abs_path)
            except OSError:
                raise PermissionError(f"Path '{abs_path}' disappeared during open.")
            if (st_path.st_dev, st_path.st_ino) != (st_fd.st_dev, st_fd.st_ino):
                raise PermissionError(
                    f"Path '{abs_path}' changed between check and open (symlink race)."
                )
            # Re-verify containment against the fully resolved real path.
            if not self._is_safe_path(os.path.realpath(abs_path)):
                raise PermissionError(
                    f"Resolved path of '{abs_path}' escapes workspace root '{self.root_dir}'."
                )
            return os.fdopen(fd, "w" if write else "r", encoding="utf-8")
        except Exception:
            os.close(fd)
            raise

    def _ensure_parent_dir(self, abs_path: str) -> None:
        """Create parent directories only when they resolve inside the sandbox."""
        parent = os.path.dirname(abs_path)
        if os.path.islink(parent):
            raise PermissionError(f"Parent directory '{parent}' is a symlink.")
        if not self._is_safe_path(os.path.realpath(parent)):
            raise PermissionError(
                f"Parent directory '{parent}' escapes workspace root '{self.root_dir}'."
            )
        os.makedirs(parent, exist_ok=True)

    def safe_read_file(self, path: str):
        abs_path = self._get_absolute_path(path)

        if not self._is_safe_path(abs_path):
            return f"Error: Path '{path}' is outside active workspace root '{self.root_dir}'."
        if not os.path.exists(abs_path):
            return f"Error: File '{abs_path}' does not exist."

        try:
            with self._open_sandboxed(abs_path, write=False) as f:
                return f.read()
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"

    def safe_write_code(self, path: str, content: str):
        abs_path = self._get_absolute_path(path)

        if not self._is_safe_path(abs_path):
            return f"Error: Path '{path}' is outside active workspace root '{self.root_dir}'."

        try:
            self._ensure_parent_dir(abs_path)
            with self._open_sandboxed(abs_path, write=True) as f:
                f.write(content)
            return f"Successfully wrote to '{abs_path}'"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"