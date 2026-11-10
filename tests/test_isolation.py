def test_cwd_is_tmp():
    import os
    cwd = os.getcwd()
    assert "/tmp" in cwd or "pytest" in cwd, f"cwd leaked: {cwd}"

def test_can_write_freely():
    with open("scratch.txt", "w") as f:
        f.write("hello")
    assert open("scratch.txt").read() == "hello"
