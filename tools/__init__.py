"""
DifferiX Tools Package - Core Refactoring Utilities

Exports modular single-responsibility tools for AST operations, file operations,
and structured code modifications. All functions return structured dictionaries
with explicit error handling.
"""

from tools.ast_ops import (
    validate_ast_syntax,
    get_available_symbols,
    extract_function_source,
    extract_class_source,
    validate_symbol_replacement,
)
from tools.tool_ops import (
    idempotent_write_file,
    run_py_compile_check,
    extract_fenced_code_blocks,
    parse_search_replace_blocks,
    validate_file_size_limits,
)

__all__ = [
    "validate_ast_syntax",
    "get_available_symbols",
    "extract_function_source",
    "extract_class_source",
    "validate_symbol_replacement",
    "idempotent_write_file",
    "run_py_compile_check",
    "extract_fenced_code_blocks",
    "parse_search_replace_blocks",
    "validate_file_size_limits",
]