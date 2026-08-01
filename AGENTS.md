# AGENTS.md

Guidance for AI agents and developers working in the **jarvis** repository.

## Repository status

This repository is currently a **greenfield scaffold**. It contains only `README.md` and agent setup files. There is no application code, dependency manifest, test suite, or service definition yet.

When application code is added, update this file with concrete run, test, and lint commands.

## Cursor Cloud specific instructions

### Services

| Service | Required? | Notes |
|---------|-----------|-------|
| None | — | No runnable services exist yet. |

### Toolchain (pre-installed on Cloud Agent VMs)

The Cursor Cloud Agent VM provides:

- **Git** 2.43+
- **Node.js** 22.x (via nvm at `/home/ubuntu/.nvm/versions/node/v22.22.2/bin/node`; `node` on PATH may report a different patch version)
- **npm** 10.x
- **Python** 3.12

No repository-specific dependency installation is required until manifests such as `package.json`, `requirements.txt`, or `pyproject.toml` are added.

### Verify the environment

From the repository root:

```bash
bash scripts/verify-environment.sh
```

This confirms the toolchain and git remote are available. It does not start any application.

### Lint, test, build, and run

Not applicable yet. Once code is added, document the commands here (for example `npm test`, `make lint`).

### Gotchas

- **Empty repo**: Do not assume frameworks, databases, or Docker services exist until they appear in the repo.
- **Update script**: The VM startup update script is intentionally minimal (`true`) because there are no dependencies to refresh.
- **Secrets**: No environment variables or API keys are required for the current scaffold.
