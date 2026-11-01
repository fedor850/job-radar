# Claude Project for applications

Job Radar finds the jobs. This folder sets up the second half: a **Claude Project** (claude.ai → Projects) that
turns each job into a ready application. That means a fit check, a few yes/no questions, a tailored one-page CV, a cover
letter or short motivation text only when the ad asks for one, and referral messages.

## How it connects to the bot

1. On a job card, press **📋 For Claude**. The bot sends a `.md` file with the full ad, its score, how to
   apply, and a line saying which documents to prepare (for example *CV + 3 sentences, no cover letter*).
2. Forward that file to a **new chat in your project**.
3. Claude checks the fit, asks up to four closed questions in one go, and builds the documents with
   [`cv_builder`](../cv_builder/README.md) in your design.
4. You review, apply, and press **✅ Applied** in the bot so the follow-up reminder is set.

## Setup (15 minutes)

1. claude.ai → **Projects → New project**. Give it the name you put in `config.yaml → candidate.claude_project`.
2. **Instructions:** paste [`project_instructions.md`](project_instructions.md) and replace the `<…>` placeholders.
3. **Files** (Add content → upload):
   | File | From |
   |---|---|
   | `Master_Profile_Facts.md` | Your facts, written from [`master_profile_template.md`](master_profile_template.md). The **only** source of facts for every document. |
   | `Application_Workflow.md` | [`application_workflow.md`](application_workflow.md) |
   | `CV_and_Cover_Letter_Style.md` | [`cv_and_cover_letter_style.md`](cv_and_cover_letter_style.md), with your contact line and naming |
   | `Country_Notes.md` | [`country-notes/austria.md`](country-notes/austria.md) or your own country's equivalent |
   | `cv_builder.py`, `resume_template.docx`, `cv_example.json` | [`../cv_builder/`](../cv_builder/) (your own template if you have one) |
   | `owner.json` | your name, contact line and content rules ([`../cv_builder/owner.example.json`](../cv_builder/owner.example.json)) |
4. Optional: if you use the Claude desktop app with access to a folder on your computer, tell the project where to save
   applications (for example `~/Applications/<date> <Company> - <Role>/`).

## Why the rules look strict

- **One source of facts.** Claude never invents or embellishes. Missing facts lead to a question, not a guess.
  Every new fact you confirm is added to the master profile, so it is asked only once.
- **Closed questions, all at once.** "Have you used Tableau? Yes/No" is answered in seconds; open questions are not.
- **Exactly what the ad asks for.** No cover letter when the ad says "CV only", and three sentences when it asks
  for three sentences.
- **One page, one design.** The builder fails when the CV runs over one page and flags your own forbidden phrases
  (e.g. a grant amount you must not disclose).
