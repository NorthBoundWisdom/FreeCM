# Owner-Managed FreeCM Latest Tracking

Use this policy only in a downstream repository whose owner has explicitly
chosen direct primary-branch integration without pull requests.

- Track the `FreeCM` submodule's `master` branch in `.gitmodules`.
- At the start of routine agent maintenance, confirm the host worktree is clean
  and the current branch is the host's existing primary branch. Do not create a
  temporary, agent-owned, dependency-update, or feature branch.
- Initialize the recorded submodule when needed, then refresh latest from the
  host repository root:

  ```bash
  git submodule update --init --recursive FreeCM
  git submodule update --remote --checkout FreeCM
  ```
- Never run `git -C FreeCM pull`. The submodule normally has detached HEAD, and
  the parent gitlink is the change that the host must review and record.
- If `git diff --submodule -- FreeCM` is empty, stay silent. Do not emit an
  outdated warning, create an empty commit, push, or open a pull request.
- If the FreeCM gitlink changed, run
  `python3 -m repomgrcpp.tools.repo_tool check-lock-compat --repo-root .` when
  the host uses the C++ adapter, plus the host's smallest meaningful workflow
  validation. Use the equivalent host-native checks for non-C++ adapters.
- After validation succeeds, commit the gitlink and any required compatibility
  edits on the existing host primary branch and push that branch directly. Do
  not open a pull request for a FreeCM-only refresh.
- If validation fails, do not commit or push the new gitlink. Report the failure
  and leave publication to the developer.
- If either the host or `FreeCM/` contains unrelated uncommitted changes, stop
  the automatic refresh and report the dirty paths instead of mixing them into
  the update.
