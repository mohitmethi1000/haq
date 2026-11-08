# Test results

Start here:

- [main_set.md](main_set.md): 50 cases, passed 44 of 50.
- [second_set.md](second_set.md): 22 separate cases (26 runs), passed 19 of 26. This is the fairer number.

Each page has a summary, a comparison with plain chatbots, and every case with its input, expected behaviour and Haq's output.

These pages are generated from the unedited JSON by `evals/make_readable.py`. The originals are in `dev/` and `heldout/`: `pipeline/` is Haq, `baseline/` is a plain chatbot, `careful/` is a chatbot with a careful prompt, and `grades.json` has the scores.
