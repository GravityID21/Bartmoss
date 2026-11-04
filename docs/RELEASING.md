# Releasing

Releases are built and published by `.github/workflows/release.yml` when a `v*` tag is pushed. It checks that the tag matches `bartmoss.__version__`, runs the unit tests, builds the sdist and wheel, publishes them to PyPI with trusted publishing (no token in the repo) and creates a GitHub release with the files attached.

## One-time setup

1. On PyPI, add a trusted publisher for the `bartmoss` project (Account settings → Publishing → Add a pending publisher): owner `GravityID21`, repository `Bartmoss`, workflow `release.yml`, environment `pypi`.
2. On GitHub, create an environment named `pypi` (Settings → Environments). Adding yourself as a required reviewer makes every publish wait for a click.

## Each release

1. Make sure CI is green on `main` and `python evals/validate.py` reports no invalid cases.
2. Set the version in `bartmoss/__init__.py` (the only place it's written; `pyproject.toml` reads it).
3. Move the `CHANGELOG.md` entry from "unreleased" to today's date.
4. Optionally build locally first: `python -m build && twine check dist/*`, then install the wheel in a fresh venv and run `bartmoss --version` and `bartmoss --model mock:echo tb some_design.sv`.
5. Commit, then tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
6. Afterwards, set the version to the next `.dev0` (e.g. `0.2.0.dev0`) so source installs don't claim to be the release.

PyPI shows `README.md` as the project page, so links and images in it are absolute GitHub URLs.
