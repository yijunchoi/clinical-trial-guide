# Clinical Trial Guide

A chat agent that helps people find and understand clinical trials, written for
users with no medical background.

ClinicalTrials.gov is the complete, free, public record of clinical research in
the US. It is also written for researchers. Someone who was diagnosed last week
lands on it and immediately hits words like *interventional*, *double-blind* and
*exclusion criteria*, and gives up. This agent sits between that database and
that person: it searches the real data, then explains it in plain English.

**Live app:** <!-- paste your Cloud Run URL here after deploying -->

## Try these three

1. **"My mother was just diagnosed with type 2 diabetes. Are there any trials
   recruiting near New York?"**
   Searches ClinicalTrials.gov and returns real recruiting trials with their
   locations and NCT IDs.

2. **"What does Phase 3 mean, and could she join the first one you listed?"**
   Explains the term from the glossary, then pulls the eligibility rules for
   that specific trial. Note that "the first one you listed" only works because
   the agent remembers the previous turn.

3. **"She's already taking metformin; what are the side effects?"**
   Looks up the official FDA label for the drug and summarizes the warnings.

## The four tools

| Tool | Source | What it does |
|---|---|---|
| `search_clinical_trials` | ClinicalTrials.gov API v2 | Finds trials by condition, location and recruiting status |
| `get_trial_details` | ClinicalTrials.gov API v2 | Pulls one trial in depth, including who is eligible to join |
| `check_drug_warnings` | openFDA drug label API | Official FDA warnings and side effects for an approved drug |
| `explain_trial_term` | Local glossary | Translates 26 clinical-trial terms into plain English |

Three tools call live external APIs. Neither API requires a key, so there are no
secrets in this repository.

`explain_trial_term` is the original tool. Gemini already knows what "Phase 2"
means; the point of the tool is *control*. Without it the model improvises a
different explanation every time, sometimes drifting back into jargon. With it,
every user gets the same wording, written deliberately for someone who is
frightened and not in a state to absorb technical language. The glossary covers
the exact vocabulary the other three tools put on screen: phase, status,
sponsor, enrollment, eligibility.

## How it works

`app.py` holds the agent loop. Each turn, the whole conversation plus the tool
descriptions go to Gemini. If the model asks for a tool, the harness, not the
model, runs it, appends the result, and loops again, until the model answers
with text instead of a request. Sessions are stored per `session_id`, so the
agent follows the conversation and separate users never see each other's.

Every tool returns a JSON string, and returns `{"error": ...}` on failure rather
than raising. An exception would kill the agent loop mid-conversation; an error
message lets the model recover, apologize, or try a different approach.

## Running it locally

```bash
uv run app.py
```

Then open http://localhost:8000

Requires a GCP project with billing and the Vertex AI API enabled, and
`gcloud auth application-default login`.

## Built with

FastAPI, LiteLLM, and Gemini (`vertex_ai/gemini-3.5-flash-lite`), deployed to
Google Cloud Run with continuous deployment from GitHub.

## Not medical advice

This agent surfaces public information and explains terminology. It does not
recommend treatments and does not advise anyone to join a trial. Those
conversations belong with a doctor.
