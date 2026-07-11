# Release Process

1. Update `VERSION`.
2. Run `python3 scripts/sync-version.py`.
3. Run local validation:

   ```bash
   python3 -m pip install -e ".[dev]"
   python3 -m pip install build
   python3 -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
   python3 scripts/check-version-consistency.py
   python3 -m mypy
   python3 -m ruff check freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
   python3 -m black --check freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
   python3 -m coverage run -m unittest discover -s tests -v
   python3 -m coverage report
   python3 -m bandit -q -r freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts
   python3 -m pip_audit . --progress-spinner off
   python3 -m build
   python3 scripts/smoke_installed_wheel.py --dist-dir dist
   cd vscode-extension
   npm test
   npm audit --omit=optional
   npm run package
   npm run smoke:vsix
   cd ..
   git diff --check
   ```

   On headless Linux, run the installed VSIX activation smoke as
   `xvfb-run -a npm run smoke:vsix`.

4. Commit with the shared hook message format.
5. Tag the release as `v<version>`.
6. Push `master` and the tag.

GitHub Releases with automatically generated release notes are the canonical
release history. Do not maintain a duplicate changelog in the repository.

Tag builds create platform VSIX artifacts named
`FreeCM_<platform>_v<version>.vsix`. Each freshly built artifact is inspected,
installed into an isolated VS Code profile, activated, and checked for its core
commands before upload to GitHub Releases. The wheel smoke installs with the
package index disabled, executes every installed console entry point, and loads
the packaged CMake modules and preset resources.

Extension compilation clears `out/` before TypeScript emits files so removed
sources cannot survive in a VSIX. Packaging includes only declared runtime
dependency packages and the smoke inspector enforces an archive allowlist. The
compressed VSIX budget is 750 KiB, the unpacked budget is 1 MiB, and the PNG
marketplace icon must remain at most 256x256 and 100 KiB. Change these budgets
only with an intentional release-size review.
