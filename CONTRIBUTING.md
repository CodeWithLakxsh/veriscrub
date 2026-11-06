# Contributing to VeriScrub

Thanks for helping! Bug reports, format requests and pull requests are all welcome.

## Set up

```bash
git clone https://github.com/CodeWithLakxsh/veriscrub && cd veriscrub
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[test]"
veriscrub doctor                                  # install ExifTool/FFmpeg if it reports them missing
python -m pytest -q -rs
```

Tests that need ExifTool, FFmpeg or pikepdf are skipped when the tool is missing. CI runs all of them on Linux, macOS and Windows.

## Ground rules

- **Never commit real personal files.** Every fixture is synthetic and built by `tests/fixtures/build.py` from constants. Add new fixtures there.
- **A format is only supported once it is verified.** A new handler must re-open its own output and prove that the targeted fields are gone and the content is unchanged. Add tests for the clean path, the skip paths and malformed input.
- **Keep the honesty rules.** Report `partial`, `skipped` or `unsupported` rather than guessing. Never add wording that claims an output is human-made, undetectable or free of hidden signals.
- **Stay offline.** No network calls, ever.
- **Avoid new runtime dependencies** unless they are optional extras.
- Keep `skills/veriscrub/SKILL.md` short. Put detail in `skills/veriscrub/references/`.

## Pull requests

1. Open an issue first for larger changes.
2. Add or update tests, and run `python -m pytest -q`.
3. Add a line to `CHANGELOG.md` under an `Unreleased` heading.

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml`, `src/veriscrub/__init__.py` and the `SKILL.md` metadata.
2. Move the changelog entries under the new version.
3. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`. The Release workflow builds the package, smoke-tests it, and publishes a GitHub Release.
