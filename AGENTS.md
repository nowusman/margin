## RULES:

1. Keep code simple and modular, 1200 lines max for code files.

2. ABSOLUTELY NO over engineering.

3. Don’t invent new patterns if there’s already a solution in the codebase.

4. Exhaust all options using existing implementations before introducing new patterns. No code duplications.

5. If introduce a new pattern to replace an old one, remove the old implementation.

6. Always a clean implementation with 100% code migration, no backward competibility or fallbacks. Abdolutely NO hacks.

7. Zero tolerance for legacy and dead code; do a 100% cleanup.

8. Python code is auto-formatted with **Black** and **isort**; **flake8**.

9. Use **type hints** throughout Python code and keep imports sorted.

10. Keep responses short and simple

11. Avoid showing code snippets.

12. Avoid writing one-time scripts in permanent files.

13. Don't mock data except for tests.
