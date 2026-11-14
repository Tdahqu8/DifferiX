# test_apply_diff.py
import json
from agents.editor import apply_diff_blocks


def json_block(obj: dict) -> str:
    """Собирает ```json ... ``` блок с корректным экранированием."""
    return "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


ORIGINAL = '''
def hello():
    return 'old'

def goodbye():
    return 'bye'
'''


def test_replace_function():
    diff = json_block({
        "operation": "replace_function",
        "name": "hello",
        "new_body": "def hello():\n    return 'new value'",
    })
    res = apply_diff_blocks(ORIGINAL, diff, target_file="test.py")

    print("Mode:", res["mode"])
    print("OK:", res["ok"])
    assert res["ok"], res
    assert "return 'new value'" in res["code"]
    assert "return 'old'" not in res["code"]
    assert "def goodbye" in res["code"], "goodbye was destroyed!"
    print("replace_function OK")


def test_anti_destructive():
    diff = json_block({
        "operation": "replace_function",
        "name": "hello",
        "new_body": "def hello():\n    # ... existing code",
    })
    res = apply_diff_blocks(ORIGINAL, diff, target_file="test.py")

    assert not res["ok"], "Should have rejected stub!"
    print("Anti-stub OK:", res["failed"][0]["reason"][:60])


def test_insert_after():
    diff = json_block({
        "operation": "insert_after",
        "anchor": "def goodbye():",
        "code": "def inserted():\n    return 42",
    })
    res = apply_diff_blocks(ORIGINAL, diff, target_file="test.py")

    assert res["ok"], res
    assert "def inserted()" in res["code"]
    print("insert_after OK")


def test_search_replace_fallback():
    diff = """<<<<<<< SEARCH
def goodbye():
    return 'bye'
=======
def goodbye():
    return 'see ya'
>>>>>>> REPLACE"""
    res = apply_diff_blocks(ORIGINAL, diff, target_file="test.py")

    assert res["ok"], res
    assert "'see ya'" in res["code"]
    print("SEARCH/REPLACE OK")


if __name__ == "__main__":
    test_replace_function()
    test_anti_destructive()
    test_insert_after()
    test_search_replace_fallback()
    print("\n=== ALL apply_diff_blocks TESTS PASSED ===")