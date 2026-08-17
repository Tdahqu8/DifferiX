import os

class FolderSandbox:
    def __init__(self, project_folder_path):
        self.allowed_folder = os.path.abspath(project_folder_path)

    def is_safe(self, relative_path):
        target_path = os.path.abspath(os.path.join(self.allowed_folder, relative_path))
        return target_path.startswith(self.allowed_folder)

    def safe_read_file(self, relative_path):
        if not self.is_safe(relative_path):
            return "Error: attempt to break from the sandbox"
        full_path = os.path.join(self.allowed_folder, relative_path)
        if not os.path.exists(full_path):
            return f"Error: file not found: {relative_path}"
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def safe_write_code(self, relative_path, code_content):
        if not self.is_safe(relative_path):
            return "Safety issue."
        full_path = os.path.join(self.allowed_folder, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(code_content)
        return f"Code successfully implemented: {relative_path}"