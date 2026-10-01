"""The four tools my agent can call, plus the descriptions the model reads.

Every tool returns a JSON string. When something goes wrong it returns
{"error": "..."} explaining what happened, instead of raising an exception.
A crash would stop the agent mid-conversation; an error message lets the
model tell the user or try something else.
"""

import json

import requests

CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"

# ClinicalTrials.gov only accepts these exact strings for a status filter.
VALID_STATUSES = {
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
    "COMPLETED",
    "TERMINATED",
    "WITHDRAWN",
    "SUSPENDED",
}

def _readable_phase(design: dict) -> str:
    """The API returns ["NA"] for non-drug studies, which reads like a
    missing value. Spell it out so the model can't mistake it for status."""
    phases = design.get("phases", [])
    if not phases or phases == ["NA"]:
        return "Not applicable (this study is not testing a drug)"
    return ", ".join(phases)

# --- Tool 1: search trials (ClinicalTrials.gov) ---


def search_clinical_trials(condition: str, location: str = "", status: str = "RECRUITING") -> str:
    """Find clinical trials for a medical condition, optionally near a place."""
    status = (status or "RECRUITING").upper().replace(" ", "_")
    if status not in VALID_STATUSES:
        return json.dumps({
            "error": f"'{status}' is not a valid status.",
            "valid_statuses": sorted(VALID_STATUSES),
        })

    params = {
        "query.cond": condition,
        "filter.overallStatus": status,
        # query.locn only ranks results by location, it does not filter them,
        # so we over-fetch and filter by site ourselves below.
        "pageSize": 30,
        "countTotal": "true",
    }
    if location:
        params["query.locn"] = location

    try:
        data = requests.get(CTGOV_URL, params=params, timeout=15).json()
    except requests.RequestException as e:
        return json.dumps({"error": f"ClinicalTrials.gov did not respond: {e}"})
    except ValueError:
        return json.dumps({"error": "ClinicalTrials.gov returned something that was not JSON."})

    studies = data.get("studies", [])
    if not studies:
        return json.dumps({
            "result": "No trials matched.",
            "hint": "Try a broader condition name, drop the location, or use status "
                    "'ALL'-like options such as NOT_YET_RECRUITING.",
            "searched_for": {"condition": condition, "location": location, "status": status},
        })

    results = []
    for study in studies:
        protocol = study.get("protocolSection", {})
        ident = protocol.get("identificationModule", {})
        design = protocol.get("designModule", {})
        locations = protocol.get("contactsLocationsModule", {}).get("locations", [])

        # Check every site, not just the first few: a big trial can list 700
        # sites, and the one near the user may be far down the list.
        nearby, others = [], []
        seen = set()
        for site in locations:
            label = ", ".join(p for p in (site.get("city"), site.get("state")) if p)
            if not label or label in seen:
                continue
            seen.add(label)
            # Match on country too, but leave it out of the label to keep it short.
            place = f"{label}, {site.get('country', '')}".lower()
            if location and location.lower() in place:
                nearby.append(label)
            else:
                others.append(label)

        if location and not nearby:
            continue
        # Put the user's sites first, so they survive the cut to 8 below.
        cities = nearby + others

        results.append({
            "nct_id": ident.get("nctId"),
            "title": ident.get("briefTitle"),
            "status": protocol.get("statusModule", {}).get("overallStatus"),
            "phase": _readable_phase(design),
            "enrollment": design.get("enrollmentInfo", {}).get("count"),
            "sponsor": protocol.get("sponsorCollaboratorsModule", {})
                               .get("leadSponsor", {}).get("name"),
            "example_sites": cities[:8],
            "total_sites": len(locations),
        })
        if len(results) >= 5:
            break

    if not results:
        return json.dumps({
            "result": f"No trials were found with a site in '{location}'.",
            "hint": "Try a nearby larger city, the state name, or search with no location.",
            "searched_for": {"condition": condition, "location": location, "status": status},
        })

    return json.dumps({
        "matched_condition": data.get("totalCount"),
        "showing": len(results),
        "trials": results,
    })


# --- Tool 2: one trial in depth (ClinicalTrials.gov) ---


def get_trial_details(nct_id: str) -> str:
    """Get the full picture of one trial: what it tests, and who can join."""
    nct_id = (nct_id or "").strip().upper()
    if not nct_id.startswith("NCT"):
        return json.dumps({
            "error": f"'{nct_id}' is not a trial ID.",
            "hint": "Trial IDs look like NCT01730534. Use search_clinical_trials to find one.",
        })

    try:
        response = requests.get(f"{CTGOV_URL}/{nct_id}", timeout=15)
        if response.status_code == 404:
            return json.dumps({"error": f"No trial exists with ID {nct_id}."})
        protocol = response.json().get("protocolSection", {})
    except requests.RequestException as e:
        return json.dumps({"error": f"ClinicalTrials.gov did not respond: {e}"})
    except ValueError:
        return json.dumps({"error": "ClinicalTrials.gov returned something that was not JSON."})

    eligibility = protocol.get("eligibilityModule", {})
    criteria = eligibility.get("eligibilityCriteria", "")

    interventions = [
        f"{item.get('type', '?')}: {item.get('name', '?')}"
        for item in protocol.get("armsInterventionsModule", {}).get("interventions", [])
    ]

    return json.dumps({
        "nct_id": nct_id,
        "title": protocol.get("identificationModule", {}).get("briefTitle"),
        "summary": protocol.get("descriptionModule", {}).get("briefSummary", "")[:1200],
        "status": protocol.get("statusModule", {}).get("overallStatus"),
        "phase": _readable_phase(protocol.get("designModule", {})),
        "conditions": protocol.get("conditionsModule", {}).get("conditions", []),
        "interventions": interventions,
        "who_can_join": {
            "min_age": eligibility.get("minimumAge", "Not specified"),
            "max_age": eligibility.get("maximumAge", "Not specified"),
            "sex": eligibility.get("sex", "ALL"),
            "healthy_volunteers": eligibility.get("healthyVolunteers"),
            # This field is long free text; truncate so it doesn't swamp the context.
            "criteria_text": criteria[:1500],
        },
        "more_info": f"https://clinicaltrials.gov/study/{nct_id}",
    })


# --- Tool 3: drug safety info (openFDA) ---


def check_drug_warnings(drug_name: str):
    """Look up official FDA label warnings and side effects for an approved drug."""
    if not drug_name or not drug_name.strip():
        return json.dumps({"error": "No drug name given."})

    drug_name = drug_name.strip()
    # Try brand name first (Tylenol), then generic name (acetaminophen).
    queries = [
        f'openfda.brand_name:"{drug_name}"',
        f'openfda.generic_name:"{drug_name}"',
    ]

    for query in queries:
        try:
            response = requests.get(
                OPENFDA_LABEL_URL, params={"search": query, "limit": 1}, timeout=15
            )
            if response.status_code == 404:
                continue  # openFDA uses 404 to mean "no match", so try the next query
            results = response.json().get("results", [])
        except requests.RequestException as e:
            return json.dumps({"error": f"openFDA did not respond: {e}"})
        except ValueError:
            return json.dumps({"error": "openFDA returned something that was not JSON."})

        if not results:
            continue

        label = results[0]
        openfda = label.get("openfda", {})

        def first(field, limit=1500):
            value = label.get(field)
            return value[0][:limit] if value else None

        return json.dumps({
            "drug": drug_name,
            "brand_names": openfda.get("brand_name", [])[:5],
            "generic_names": openfda.get("generic_name", [])[:5],
            "warnings": first("warnings") or first("boxed_warning"),
            "side_effects": first("adverse_reactions"),
            "do_not_use_if": first("do_not_use", 800),
            "source": "FDA drug label via openFDA",
        })

    return json.dumps({
        "error": f"No FDA label found for '{drug_name}'.",
        "hint": "Check the spelling, or try the generic name instead of the brand name "
                "(for example 'acetaminophen' rather than 'Tylenol').",
    })


# --- Tool 4: the jargon translator (no API -- definitions live in this file) ---

GLOSSARY = {
    "phase 1": "The very first time a drug is tried on people — usually 20 to 100 of them. They're checking "
               "whether it's safe and what dose to use, not whether it works yet. If you join one, you're very early, and a lot is still unknown.",
    "phase 2": "A medium test, usually 100-300 people who have the condition. The question is 'does this actually seem to help?'",
    "phase 3": "A large test, often 300-3,000 people. The question is 'is this better than "
               "the treatment we already have?' This is the stage before FDA approval.",
    "phase 4": "Happens after the drug is already approved and sold. "
               "Researchers keep watching for rare or long-term side effects.",
    "na": "Not applicable - this study isn't testing a drug, so the phase "
          "system doesn't apply. Common for studies of behavior, diet, or devices.",
    "recruiting": "This trial is open and still taking people. It's the only status where you can actually apply. "
                   "Everything else is either not open yet or already closed.",
    "not yet recruiting": "The trial is approved but has not opened its doors yet. Worth checking back later.",
    "active not recruiting": "The trial is running, but it already has all the participants it needs. "
                            "You cannot join.",
    "completed": "The trial has finished. Results may be published.",
    "terminated": "The trial was stopped early and will not restart -- sometimes for safety "
                  "reasons, sometimes because it wasn't working.",
    "intervention": "The thing being tested: a drug, a device, a surgery, even an exercise program.",
    "placebo": "A fake treatment with no active ingredient, like a sugar pill. It lets "
               "researchers tell a real effect apart from wishful thinking.",
    "randomized": "Participants are sorted into groups by chance, not by choice. This stops "
                  "researchers from accidentally stacking one group with healthier people.",
    "double blind": "Neither you nor your doctor knows whether you got the real treatment or "
                    "the placebo, so nobody's expectations can tilt the results.",
    "sponsor": "Whoever is paying for and running the trial -- a drug company, a university, "
               "or a government agency.",
    "eligibility criteria": "The checklist of who can and cannot join, based on age, "
                            "diagnosis, other medications, and so on.",
    "inclusion criteria": "The things you must have to join, such as a specific diagnosis or age range.",
    "exclusion criteria": "The things that disqualify you, such as being pregnant or taking a "
                          "conflicting medication.",
    "healthy volunteers": "Whether people without the condition can also join. Common in "
                          "Phase 1 safety trials.",
    "nct id": "The trial's unique ID number, always starting with NCT. Think of it as the "
              "trial's license plate.",
    "enrollment": "How many people the trial plans to include, or did include.",
    "observational": "Researchers watch and record what happens naturally. Nobody is given a new treatment.",
    "interventional": "Researchers actively give participants a treatment and measure what "
                      "happens. This is what most people mean by 'clinical trial'.",
    "open label": "Everyone knows who is getting what. The opposite of blind.",
    "arm": "One group within a trial. A trial might have a 'drug arm' and a 'placebo arm'.",
    "endpoint": "The specific thing the trial measures to decide if the treatment worked, "
                "such as blood pressure after 6 months.",
}

# Words people actually type, mapped to the glossary key they mean.
ALIASES = {
    "phase i": "phase 1", "phase ii": "phase 2",
    "phase iii": "phase 3", "phase iv": "phase 4",
    "phase1": "phase 1", "phase2": "phase 2",
    "phase3": "phase 3", "phase4": "phase 4",
    "blinded": "double blind", "blind": "double blind",
    "double-blind": "double blind",
    "primary endpoint": "endpoint", "outcome": "endpoint",
    "criteria": "eligibility criteria",
    "sugar pill": "placebo",
    "nct": "nct id", "nct number": "nct id",
    "not applicable": "na", "phase na": "na", "no phase": "na",
}


def explain_trial_term(term: str):
    """Translate one piece of clinical-trial jargon into plain English."""
    if not term or not term.strip():
        return json.dumps({"error": "No term given.", "available_terms": sorted(GLOSSARY)})

    key = term.strip().lower().replace("_", " ").replace(",", "")
    key = ALIASES.get(key, key)

    if key in GLOSSARY:
        return json.dumps({"term": term, "plain_english": GLOSSARY[key]})

    # Not an exact match - offer the closest thing rather than a dead end,
    # so the model can suggest something instead of giving up.
    close = [k for k in GLOSSARY if key in k or k in key]
    return json.dumps({
        "error": f"'{term}' is not in the curated glossary.",
        "did_you_mean": close[:5],
        "hint": "Explain this term yourself in plain English, assuming the "
            "user has no medical background.",
    })


# --- What the model sees ---------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_clinical_trials",
            "description": (
                "Search ClinicalTrials.gov for trials studying a medical condition. "
                "Use this whenever the user asks what trials exist for a disease or "
                "symptom, or asks about trials near a place. Returns up to 5 trials "
                "with their ID, phase, status, sponsor and example locations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "condition": {
                        "type": "string",
                        "description": "The disease or condition, e.g. 'type 2 diabetes', "
                                       "'breast cancer', 'migraine'.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional. A city, state or country to search near. "
                                       "Spell out the full name, e.g. 'New York' not 'NY' -- "
                                       "ClinicalTrials.gov stores full state names, so "
                                       "abbreviations match the wrong cities.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional, defaults to RECRUITING. One of: RECRUITING, "
                                       "NOT_YET_RECRUITING, ENROLLING_BY_INVITATION, "
                                       "ACTIVE_NOT_RECRUITING, COMPLETED, TERMINATED, "
                                       "WITHDRAWN, SUSPENDED.",
                    },
                },
                "required": ["condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trial_details",
            "description": (
                "Get full details for one specific trial, including what it is testing and "
                "the eligibility rules for who can join. Use this after "
                "search_clinical_trials when the user asks about a particular trial, or "
                "asks whether they would qualify."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nct_id": {
                        "type": "string",
                        "description": "The trial's ID, e.g. 'NCT01730534'.",
                    },
                },
                "required": ["nct_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_drug_warnings",
            "description": (
                "Look up the official FDA label for an approved drug: its warnings, known "
                "side effects, and who should not take it. Use this when the user asks if a "
                "drug is safe, what its side effects are, or mentions a drug by name. Works "
                "for approved drugs only, not experimental ones still in trials."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "drug_name": {
                        "type": "string",
                        "description": "Brand or generic drug name, e.g. 'Tylenol' or "
                                       "'metformin'.",
                    },
                },
                "required": ["drug_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_trial_term",
            "description": (
                "Translate a clinical-trial or medical research term into plain English a "
                "non-expert can understand. Use this whenever jargon appears that the user "
                "may not know - such as 'Phase 2', 'placebo', 'double blind' or "
                "'exclusion criteria' - including when the term came up in your own "
                "previous answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {
                        "type": "string",
                        "description": "The term to explain, e.g. 'phase 2' or 'randomized'.",
                    },
                },
                "required": ["term"],
            },
        },
    },
]

TOOL_MAP = {
    "search_clinical_trials": search_clinical_trials,
    "get_trial_details": get_trial_details,
    "check_drug_warnings": check_drug_warnings,
    "explain_trial_term": explain_trial_term,
}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
