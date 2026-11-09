# Repository Guidelines

## Project Structure & Module Organization

This guide covers `Week12/` within the larger coursework repository.

- `作业1/`: hallway fire-risk image analyzer. `frontend.py` provides Streamlit UI, `backend.py` exposes FastAPI, `my_agents.py` configures the model, and `models.py` defines structured output. Tests live alongside these modules.
- `作业2/src/sql_agent/`: LangChain SQL workflow. Keep model agents in `agents/`, database catalog/validation/execution in `db/`, and orchestration in `workflow.py`.
- `作业2/ref/`: reference examples and the `chinook.db` SQLite fixture.
- `作业截图/`: submission screenshots. Notebooks contain reference material; the root `作业` file describes assignment requirements.

## Build, Test, and Development Commands

Use an isolated Python environment. There is no shared build command. From `Week12/`, run these PowerShell commands for the image application:

```powershell
cd 作业1
python -m pip install -r requirements-frontend.txt
python -m pip install fastapi uvicorn python-multipart openai-agents Pillow
python -m uvicorn backend:app --port 8000
```

In another terminal, enter `作业1/` and run `python -m streamlit run frontend.py --server.port 8501 --server.maxUploadSize 10`.

For the SQL agent, start from `Week12/`:

```powershell
cd 作业2
python -m pip install langchain langchain-openai sqlalchemy pydantic
$env:PYTHONPATH = (Resolve-Path ./src).Path
python -m sql_agent "List the top five artists by album count"
```

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` modules/functions, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Add type hints to new interfaces and preserve structured Pydantic outputs. Follow surrounding Chinese comments and user-facing messages. No formatter or linter configuration is present; keep changes focused and consistent.

## Testing Guidelines

From `作业1/`, run `python -m unittest discover -s . -p 'test_*.py' -v`. Tests use `unittest`, mocks, and Streamlit AppTest. Name tests `test_*.py` with `test_*` methods. Cover validation, model-output failures, and UI state changes without live API calls. No coverage threshold is configured. `作业2/tests/` currently contains cached artifacts only; add source tests for SQL changes, especially read-only validation and repair limits.

## Commit & Pull Request Guidelines

History uses concise Chinese summaries such as `第12周作业` and `目录名称修改`; no enforced prefix convention is evident. Write focused commits identifying the assignment and change. PRs should explain behavior, list validation commands/results, link relevant issues, and include screenshots for UI changes. Exclude caches, virtual environments, and unrelated coursework changes.

## Security & Configuration

Set `DEEPSEEK_API_KEY` through the environment. The image backend uses `DeepSeek_URL`; the SQL agent uses `DEEPSEEK_URL` and optional `SQL_AGENT_DB`. Never commit credentials. Preserve SQL read-only connections, validation gates, and query limits; keep `chinook.db` unchanged during tests.
