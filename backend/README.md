# Tyche Options — Backend

Options trading copilot backend powered by FastAPI, Tradier, and Google Gemini.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
uvicorn tyche.app:app --reload
```

## Test

```bash
pytest
```
