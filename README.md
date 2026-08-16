# RxExplain (Prescription Explanation Project)

This project reads short prescription text (for example, Indian shorthand) and explains it in simple patient friendly English.

It has:
- A command line tool (CLI)
- A Streamlit web app
- A rule only mode that works without LLM credentials

## 1) What you need

- Python 3.11 or newer
- pip
- Internet (only needed for LLM and live retrieval)

## 2) Setup

Run these commands from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

If you prefer requirements file:

```bash
pip install -r requirements.txt
```

## 3) Environment values to supply (.env)

Create a file named `.env` in the project root and add values like this:

```env
# Required for LLM systems (zeroshot, sota)
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com/
AZURE_OPENAI_API_KEY=YOUR_AZURE_KEY
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini

# Optional (default is already set in code)
AZURE_OPENAI_API_VERSION=2024-10-21

# Optional: if not set, it uses AZURE_OPENAI_DEPLOYMENT
AZURE_OPENAI_JUDGE_DEPLOYMENT=gpt-4.1-mini

# Optional: usually auto-detected, so you can skip this
# Use azure_openai for *.openai.azure.com
# Use openai_compat for *.services.ai.azure.com/openai/v1
# RX_CLIENT_MODE=azure_openai
```

Important:
- If you only want rule based output, you can skip `.env`.
- Endpoint format depends on your Azure resource:
	- Azure OpenAI style: `https://<name>.openai.azure.com/`
	- Foundry OpenAI compatible style: `https://<name>.services.ai.azure.com/openai/v1`

## 4) Quick health check

Run:

```bash
PYTHONPATH=src python -m rxexplain.cli doctor --offline
```

Expected result:
- Knowledge base and gold set load successfully.
- LLM check shows "NOT CONFIGURED" if `.env` is missing (this is okay for rule only mode).

## 5) Run the application (Streamlit UI)

```bash
streamlit run app/streamlit_app.py
```

Then open the local URL shown in terminal (usually `http://localhost:8501`).

How to use:
1. Paste prescription text in the text box.
2. Choose "One system" or "Compare all three".
3. Click "Explain".
4. Check tabs for Explanation, Safety checks, and Parser output.

## 6) CLI usage

Reliable form (works even if your project path has spaces):

```bash
PYTHONPATH=src python -m rxexplain.cli <command> [options]
```

Shortcut form (works after editable install in most environments):

```bash
rxexplain <command> [options]
```

Explain one prescription:

```bash
PYTHONPATH=src python -m rxexplain.cli explain --system sota --text "T. Dolo 650 1-0-1 x 5 days AF"
```

Rule-only explain (no LLM credentials needed):

```bash
PYTHONPATH=src python -m rxexplain.cli explain --system rule --text "Cap Omez 20mg 1-0-0 BF x 5 days"
```

Compare all systems on one input:

```bash
PYTHONPATH=src python -m rxexplain.cli compare --text "T. Dolo 650 1-0-1 x 5 days AF"
```

Run evaluation set:

```bash
PYTHONPATH=src python -m rxexplain.cli eval --systems rule,zeroshot,sota
```

Results are written to the `results/` folder.

## 7) Notes

- `rule` system works offline and without Azure credentials.
- `zeroshot` and `sota` require valid Azure values in `.env`.
- Retrieval cache is stored under `data/cache/`.
