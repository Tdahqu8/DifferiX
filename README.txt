You may write and run tests to verify your changes. Tests must:

1. Live under tests/ directory
2. Use the `tmp_path` fixture for any file I/O
3. Be marked with @pytest.mark.unit for pure logic tests
4. Be marked with @pytest.mark.integration for tests that touch the filesystem
5. NEVER call an LLM directly — mark such tests @pytest.mark.llm and know they
   will be skipped by default

Run your tests with: ./run_tests.sh fast
If a test fails, fix it before reporting success.