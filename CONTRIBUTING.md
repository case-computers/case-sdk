# Contributing to case-sdk

Thanks for the interest. case-sdk is small and prefers small, focused
changes.

## Quick start

```bash
git clone https://github.com/daemonlabshq/case-sdk
cd case-sdk
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mcp]"
pytest -x
```

You need:

- macOS (most automation code is macOS-only)
- Python 3.11 or newer
- Accessibility permission granted to your shell / IDE if you run live
  procedures (not required for the unit-test suite, which mocks the
  harness)

## Filing issues

- **Bug reports:** include `case-sdk --version`, what you ran, what you
  expected, what happened, and (where possible) a minimal procedure
  JSON that reproduces.
- **Feature requests:** describe the use case before the proposed API.
  We're conservative about adding new primitives to the harness.
- **Security issues:** see `SECURITY.md` — do not file public issues
  for suspected vulnerabilities.

## Pull requests

- Branch off `main`. Small, focused PRs land faster than large ones.
- Run `pytest -x` locally before opening the PR. Add tests for any
  observable behaviour change.
- Match the surrounding style — type hints on public functions, short
  docstrings on the public API surface, no comments restating what the
  code already says.
- Update `CHANGELOG.md` under `## [Unreleased]` if your change is
  user-visible.
- One PR per topic. Don't bundle a refactor with a bug fix.

## Procedure contributions

If you want to add a procedure pack (DaVinci, another app, etc.):

- Prefer JSON step lists over Python generators when possible.
- Place under `case_sdk/builtin_procedures/<pack>/<name>.json`.
- Include a `description` field — it shows in the `case-sdk procedures`
  catalog.
- If the procedure needs a manual setup step, declare
  `requires_user_action` and `user_action_prompt`.
- Validate against `docs/schemas/procedure.schema.json`.

## Licensing of contributions

case-sdk is licensed under the **Elastic License 2.0** (see `LICENSE`).
By opening a pull request you confirm that:

- You wrote the contribution yourself or have permission to submit it.
- You agree to license your contribution under the same Elastic License
  2.0 as the rest of the project.

If you cannot agree to those terms, please don't submit a PR.

## Maintainer workflow

For maintainers cutting a release:

1. Bump `version` in `pyproject.toml` and `__version__` in
   `case_sdk/__init__.py`.
2. Add a `## [x.y.z]` block in `CHANGELOG.md`.
3. Merge to `main`, tag `vx.y.z`, push the tag to `daemonlabs`.
4. The `publish.yml` workflow handles TestPyPI + PyPI uploads via OIDC
   trusted publisher.
