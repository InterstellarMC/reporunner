# Decisions

- This task creates the skeleton only; section 9 step 1 has not started.
- `requirements.txt` installs the local package with its pytest development extra;
  dependency declarations live in `pyproject.toml`. Setuptools is build tooling.
- Dependency versions will be pinned after a real environment is validated.
- `eval/repos.json` is an empty placeholder. Repository selection and evaluation
  are deferred; no results or successful runs are claimed.
- `reporunner/__main__.py` reserves the documented module entrypoint and contains
  only a docstring until CLI implementation in build step 2.
- `runs/` is created locally and ignored in Git; a fresh checkout will need the
  directory created when run logging is implemented.
