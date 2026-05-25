# Release Process

1. Update `VERSION`.
2. Run `python3 scripts/sync-version.py`.
3. Update `CHANGELOG.md`.
4. Run local validation:

   ```bash
   python3 -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
   python3 -m unittest discover -s tests -v
   python3 scripts/check-version-consistency.py
   cd vscode-extension
   npm test
   npm audit --omit=optional
   npm run package
   cd ..
   git diff --check
   ```

5. Commit with the shared hook message format.
6. Tag the release as `v<version>`.
7. Push `master` and the tag.

Tag builds create platform VSIX artifacts named
`FreeCM_<platform>_v<version>.vsix` and publish them to GitHub Releases.
