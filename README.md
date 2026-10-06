# Clinical Trial Guide

A chat agent that helps people find and understand clinical trials, designed for users who have no medical background.

ClinicalTrials.gov is a free, public register of clinical studies run by the U.S. National Institutes of Health, covering trials in the United States and abroad, and it is written for researchers. An individual who was diagnosed last week will land on the site, find terms like interventional, double-blind and exclusion criteria, and get frustrated. This chat agent acts as an intermediary between the database and the individual: it fetches the real records and explains them in plain language. Drug safety information comes from the FDA's own labels, through openFDA.

**Live app:** <https://clinical-trial-guide-git-900827490817.europe-west1.run.app>

## Three sample queries to test

1. **"My mother was just diagnosed with type 2 diabetes. Are there any trials
   recruiting near New York?"**
   Searches ClinicalTrials.gov and returns real recruiting trials with their
   locations and NCT ID (National Clinical Trial identifier, assigned to each clinical study registered on ClinicalTrials.gov).

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

Three of the four tools call live external APIs, and neither API requires a key.

`explain_trial_term` is my original tool. Gemini already knows what "Phase 2" means; the aim of this tool is to keep control over the wording. Without it, the model gives an explanation that differs every time and occasionally slips back into jargon. With this tool, every user gets the same explanation, written for someone who does not know the terminology. The glossary covers the vocabulary the other three tools put on screen, such as phase, status, sponsor, enrollment, eligibility.

## How it works

`app.py` holds the agent loop. On each turn, the full conversation and the tool descriptions go to Gemini. When the model asks for a tool, the harness is what runs it, appends the result, and sends the conversation back for another round. The loop ends once the model answers with text rather than another tool request. Each conversation is stored under its own session_id, so the agent remembers what came before and two users never see each other's chats.

Every tool returns a JSON string, and on failure it returns {"error": ...} rather than raising an exception. An exception would end the loop in the middle of a conversation; an error message lets the model explain what went wrong or try a different approach.

## Running it locally

Most people should just use the live app above. To run your own copy, you need
a GCP project with billing enabled and the Vertex AI API turned on.

```bash
gcloud auth application-default login
uv run app.py
```

Then open http://localhost:8000


## Built with

FastAPI, LiteLLM, and Gemini (`vertex_ai/gemini-3.5-flash-lite`), deployed to
Google Cloud Run with continuous deployment from GitHub.

## Not medical advice

This agent shows public information and explains terminology. It does not
recommend treatments and does not advise anyone to join a trial. These issues should be discussed with a doctor.
