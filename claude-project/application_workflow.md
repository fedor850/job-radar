# Application workflow

The procedure for every job. The goal is the highest reply rate: apply fast (within 24–48 h of posting), tailor
precisely, and look for a way in through people at the same time.

## Question rule (priority)
Ask all questions in ONE batch at the start (pop-up options when available, up to 4), right after the fit check,
then work without stopping. Never list questions in the chat or leave them for the end. If you cannot ask, make a
reasonable assumption, mark it, and continue.

## 0. Input
The job comes from Job Radar (the "📋 Для Claude" button sends a `.md` with the full ad, the link, the radar's score
and a line "Prepare for this job: …"), or as a link / text. If there is no text, ask for it (LinkedIn links often
do not open).
- Check whether I already applied to this company: the line "⚠️ The tracker already has applications to this
  company" in the `.md`, or earlier application folders. If so, the new documents must not contradict the old ones
  (start date, hours, role titles). Mention it in the fit check.

## 0.1 What exactly the employer asks for (ALWAYS first)
Read how to apply and which documents are required, and deliver EXACTLY that. The bot's "Prepare …" line is a hint;
the ad decides.
- "CV only", "no cover letter", "kein Anschreiben" → NO cover letter.
- "CV + 3 sentences on why you", "a few sentences about your interest" → a short text of exactly that length, in the
  requested language, instead of a letter (offer 2 versions).
- "Cover letter / Motivationsschreiben / Anschreiben" → a full letter (step 4).
- Nothing said → CV + cover letter.
- Other items (transcript, certificates, references, photo, form, video, test): list what to attach and what is missing.
- Channel (e-mail, form, portal), address and subject line come from the ad. For e-mail applications also write the
  e-mail itself (subject + 3–5 lines).

## 1. Fit check (5–8 lines)
- What the role is and what matters most to the employer (3–5 key requirements from the text).
- Matches with the Master Profile (concrete facts, numbers).
- Gaps and risks: hours, language level, seniority, work permit, start date, pay. Quote the ad accurately and do
  not exaggerate (contractual hours are the hours in the ad; remarks about busy weeks are a separate thing).
- Verdict: apply / apply with a caveat / don't spend time, and why.
If the job is full-time but otherwise strong, offer to apply anyway and state my hours in the letter, or to ask the
recruiter first.

## 2. Questions (one pop-up, right after the fit check)
- Only closed questions ("Have you done X?") about what the employer wants and the Master Profile lacks. Put ALL of them
  in one batch (up to 4).
- If renaming a past role would clearly raise relevance, offer the names as options. I approve.
- Confirmed new facts go into Master_Profile_Facts.md immediately ("Confirmed <date> (<company>)"). Record the "no"
  answers too ("no Tableau"), so they are never asked again or claimed.
- After the answers, do everything in one pass: CV, text/letter, networking messages, saving.

## 3. CV (one page A4, in the documents' language)
- Headline for the role (e.g. "Finance & Business Analytics", "Sales & Business Development").
- Summary, 2–3 lines: who I am + 1–2 of the most relevant numbers + availability (start, hours, city).
- Skills: 2 lines, keywords from the ad (real skills only).
- Experience: 3–5 bullets per role, ordered by relevance, written as "action + what + result/number". No "I/my". Start
  each bullet with a past-tense verb.
- Education, certificates & languages, additional: shorten if space is tight.
- Build: write `cv_spec.json` (see cv_example.json), then
  `python3 cv_builder.py cv cv_spec.json <out_dir> --name <First_Last>_CV_<Company>` → .docx + .pdf.
  The script prints `Pages:`, `RULE:` lines (my hard rules: must be fixed, exit code 4) and `WARN:` lines (check).
  Exit code 3 = more than one page: shorten. Quick check without building: `python3 cv_builder.py check cv_spec.json`.
- Look at the PDF (render the page) before sending it to me.

## 4. Cover letter (≈ 250–330 words, one page): only if needed per 0.1
1. Why this company and role: something concrete (product, market, project), not generic praise.
2. The main proof for the key requirement, with numbers.
3. A second proof for another requirement.
4. Availability (start, hours, city, flexibility), languages, a short call to action.
- Build: `python3 cv_builder.py cl letter_spec.json <out_dir> --name <First_Last>_Cover_Letter_<Company>`.
- If the ad names a contact person, address them by name.
- A short motivation text (0.1) needs no template. Give it in the chat and as the e-mail body, in 1–2 versions.

## 5. Application form questions
Short and factual. "Why us?" takes 3–4 sentences. For salary expectations and the work permit, use Country_Notes.md.

## 6. Referrals and networking (in parallel)
- The bot's card has 👥 alumni / 🎯 recruiters buttons (LinkedIn searches).
- Write 2 short English messages (≤ 300 characters for a LinkedIn connection request):
  a) to an alumnus at the company: ask for a 10-minute chat or advice about the role;
  b) to the recruiter: "applied for <role>, one strong fact, available <hours> from <date>".
- No pressure and no "please refer me" in the first message.

## 7. After applying
- Remind me to press "✅ Подался" in Job Radar, especially for e-mail applications, where the company usually
  sends no confirmation that the bot could see.
- If the documents are ready but not sent yet, end with one line: what to send, where, and by when.

## 8. Interview
When an invitation arrives (the bot reports it from the inbox), prepare: the company (recent news, product), 6–8 likely
questions with STAR answers from the Master Profile, 3 questions for the employer, logistics (hours, start, permit),
and answers to any awkward points in my CV (overlapping dates, gaps).
