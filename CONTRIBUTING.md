# Contributing to Glokta

## Getting started

1. Fork the repo and create a branch from `main`.
2. Set up your environment:
   ```bash
   conda env create -f environment.yml
   conda activate glokta
   pip install -e ".[dev]"
   ```
3. Copy `.env.example` to `.env` and fill in required values.

## Running tests

```bash
make test
```

All PRs must pass `make test` and `mypy src/` before review.

## Pull requests

- Keep PRs focused — one change per PR.
- Update tests for any changed behaviour.
- Use clear commit messages that explain *why*, not just what.

## Reporting issues

Open a GitHub issue with steps to reproduce, expected behaviour, and actual behaviour. Include the output of `docker compose logs` if relevant.
