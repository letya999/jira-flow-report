# Contributing

Thanks for helping improve Jira Flow Report. Small fixes, clearer documentation and
reproducible metric bugs are useful contributions.

## Set up

```sh
uv sync --locked
git config core.hooksPath .githooks
uv run --locked pytest
uv run --locked ruff check src tests examples .githooks .repo-safety/scripts
uv build
```

Development uses Python 3.13 from `.python-version`; the package requires Python 3.12+.
Tests use synthetic histories and do not need a Jira account or `.env`.
Run `uv run --locked python examples/demo.py` to inspect the actual report template.

## Changes and commits

- `main` is the only permanent branch. External contributions can use a branch in a fork
  and open a pull request against `main`; do not add `dev` to this repository.
- Commit format: `type(scope): description`, with an optional scope.
  Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`.
- Use your own Git identity. Do not add `Co-authored-by` trailers or AI attribution.
- Keep one logical change per commit. Include a small regression test for calculation changes.
- Update both README languages when changing setup, commands or supported behavior.
- Change the packaged template and rebuild; do not edit generated HTML reports.

For a metric bug, provide the expected calculation and a minimal **synthetic** history,
including time zone, reporting boundaries and any reopenings. Include a failing test when possible.
Do not attach real Jira exports, concrete team/person configs, API tokens or report screenshots.

## Checks

CI runs tests, Ruff, the demo and package builds on Linux and Windows. It uses no Jira credentials
and uploads no report artifacts. Local hooks check commit subjects, prohibit coauthor trailers,
and reject sensitive paths even after `git add -f`.

The pre-push hook uses the installed `ai-repo-safety` command or the bundled fallback.
Secret scanning uses Gitleaks/TruffleHog when available; missing optional scanners are reported.
Hooks are local checks, not a replacement for reviewing the diff and repository history.

Code contributions are provided under the project's [MIT license](LICENSE).
