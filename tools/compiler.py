import subprocess
import os

def run_universal_build(project_path, language_type):
    """Автоматически компилирует проект и собирает чистые логи ошибок для Ревизора"""
    if not os.path.exists(project_path):
        return False, "❌ Ошибка: Путь к проекту не существует."
        
    os.chdir(project_path)
    
    commands = {
        "csharp": ["dotnet", "build", "-c", "Release"],
        "java": ["gradlew.bat", "compileJava"],
        "cpp": ["cmake", "--build", "build"],
        "python": ["python", "-m", "py_compile", "main.py"]
    }
    
    if language_type not in commands:
        return False, f"❌ Ошибка: Язык {language_type} не поддерживается авто-сборщиком."
        
    try:
        result = subprocess.run(commands[language_type], capture_output=True, text=True, timeout=45)
        if result.returncode == 0:
            return True, "🎉 Компиляция успешна! Ошибок синтаксиса нет."
        else:
            raw_output = result.stdout + "\n" + result.stderr
            error_lines = []
            
            for line in raw_output.split('\n'):
                if any(err_marker in line for err_marker in ["error CS", "error:", "Exception", "FAILED"]):
                    error_lines.append(line.strip())
            
            clean_errors = "\n".join(error_lines) if error_lines else raw_output[:1000]
            return False, f"❌ ОШИБКА СБОРКИ:\n{clean_errors}"
            
    except Exception as e:
        return False, f"❌ Критический сбой сборщика: {str(e)}"