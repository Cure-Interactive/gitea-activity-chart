# Gitea Activity Chart

Desktop app for querying Gitea activity and visualizing activity counts over time.

## Requirements

- Python 3.10+
- Network access to a Gitea instance
- A Gitea token with read access
- Dependencies from `requirements.txt`

## Install

```bash
python setup.py --venv
```

Or manually:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On Linux or macOS, activate the virtual environment with `source .venv/bin/activate`.

## Configure

On first run, the app creates `config.json` from `config_default.json`.

Set at minimum:

- `gitea.base_url`
- `gitea.token_env`, or provide a token through the UI/config

Using an environment variable such as `GITEA_TOKEN` is recommended.

## Run

```bash
python gitea_activity_chart.py
```
