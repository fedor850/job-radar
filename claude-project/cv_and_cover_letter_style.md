# CV & cover-letter style

## CV layout (template: resume_template.docx; builder: cv_builder.py)
- One page, A4.
- Header: NAME (large, bold, centred) → headline tailored to the role → "City, Country | e-mail | phone".
  <Decide and write here: LinkedIn yes/no, photo yes/no, date of birth (no, in international CVs).>
- Sections, in this order unless the role suggests otherwise: SUMMARY · SKILLS · EXPERIENCE · EDUCATION ·
  CERTIFICATIONS & LANGUAGES · ADDITIONAL. EDUCATION may go above EXPERIENCE when it is clearly stronger for the
  role. An extra section ("FINANCE PROJECTS") is fine when it carries a key requirement of the ad.
- Entry header: ORGANISATION (bold, caps) left, dates right; role (italic) left, location (italic) right.
- Bullets: 3–5 per role, each 1–2 lines (≤ ~260 characters). Start with a strong past-tense verb and end with a
  number or result when possible. No "I / my".
- Dates: "Jun. 2025 – Feb. 2026" (en dash, abbreviated month). Numbers: "EUR 50,000", "approx.", "+20%", always the same way.

## Wording rules
- Facts only from Master_Profile_Facts.md, with its numbers exactly.
- Mirror 5–8 keywords from the ad naturally (skills line, summary, bullets).
- Skills marked NO in the Master Profile are never claimed. They may appear only as "not yet, ready to learn" in a letter.
- Role titles exactly as in the Master Profile. Any change needs my approval (record approved alternatives here).
- <Your own rules: things never to state (amounts, follower counts…), preferred wordings, grade conversions.>
- cv_builder enforces the hard rules from owner.json (RULE: lines, exit code 4) and warns about the soft ones (WARN:).

## Cover letter
- Same header as the CV, date on the right, recipient block, bold subject "Application: <Role> (<hours>, from <date>)",
  or the hours/start from the ad.
- 250–330 words, 4 paragraphs: why them → proof 1 → proof 2 → availability, languages, call to action.
- Tone: confident, specific. No clichés ("I am writing to apply…", "passionate", "dynamic team").
- Salutation: "Dear Ms/Mr <Surname>," when the contact is clear, otherwise "Dear <First name> <Surname>," or
  "Dear Hiring Team,". Sign-off: "Kind regards," + full name.
- Ads in the local language: write in <English> unless asked otherwise; optionally offer a local-language version.

## File naming
- <First_Last>_CV_<Company>.pdf / .docx · <First_Last>_Cover_Letter_<Company>.pdf / .docx
- Short_Text_<Company>.md (when the ad asks for a few sentences instead of a letter)
- Folder: <YYYY-MM-DD> <Company> - <Role>/ (+ job.md, cv_spec.json, letter_spec.json)
