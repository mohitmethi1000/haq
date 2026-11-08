# Haq

A legal information tool for people in India who are owed money: unpaid salary, consumer refunds and, partly, rental deposits. It works by text or voice in English, Hindi or Hinglish. It is not a lawyer, and it says when you need one (free legal aid: 15100).

Assignment doc: [solution_doc.pdf](solution_doc.pdf). **Inputs and outputs for every test: [raw_outputs/README.md](raw_outputs/README.md).** Test cases are in `evals/`.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env   # add SARVAM_API_KEY
streamlit run app.py
```
Try a new input from the command line:
```bash
python run_case.py "Meri company ne October ki salary nahi di, 30000 thi. Main Pune mein hoon."
```
Run the tests (no model calls) with `pytest -q`, and the evals with `python evals/run_evals.py`.

## Change the model
Set `LLM_PROVIDER`, `LLM_MODEL` and `LLM_BASE_URL` plus a key in `.env`, or use Advanced settings in the app. Any OpenAI-compatible API works.

## Limits
Not reviewed by a lawyer. State rent law is not verified. Results and failures are in the doc.
