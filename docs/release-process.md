# Release Process

1. Update `VERSION`.
2. Run `python3 scripts/sync-version.py`.
3. Update `CHANGELOG.md`.
4. Run local validation:

   ```bash
   python3 -m pip install build
   python3 -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
   python3 -m unittest discover -s tests -v
   python3 scripts/check-version-consistency.py
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

5. Commit with the shared hook message format.
6. Tag the release as `v<version>`.
7. Push `master` and the tag.

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
