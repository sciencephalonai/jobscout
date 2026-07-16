# Visual QA checklist (run before opening the PR)

1. **Sidebar**: all labels visible ("Targeting …", "Refresh watchlist", "indexed", "N sources").
2. **For You**: loads in seconds; jobs are entry-level DS/ML/SWE only; sort → "Newest First" reorders;
   Date-posted pill narrows and keeps the window tag; no nurses/seniors/defense contractors.
3. **First-run**: switch Matching profile to "No active profile" → 3-step onboarding hero with
   upload CTA appears; switch back → feed returns.
4. **Profile tab**: structured cards render (Education: two schools with GPAs; 3 experience roles;
   6 projects with tech pills; 9 skill categories as pills). Edit an experience bullet → Save →
   reload → persists. "Improve bullets" shows per-bullet diff; Accept one; Save. Add a custom
   section. Open "Raw text" — text reflects your edits.
5. **Tailor gate**: on a defense/skip job, "Tailor DOCX" shows the skip conclusion with
   "Tailor anyway"; on a good match it builds directly with a download link.
6. **Setup banner**: temporarily rename a key in .env + restart → banner explains the exact fix;
   restore key.
7. **Deep match**: button returns verdict/score/gaps on any job. In For You, "Deep-match top 10"
   badges cards progressively, re-ranks tiered when done, and "Ranked by AI ✕" reverts; a second
   click analyzes the next 10.
8. **Toggles**: every switch (Eligibility modal, Work-preferences card, FilterBar sponsorship pills)
   shows the knob INSIDE the track in both states — no detached white ball.
9. **Resume library**: upload the same file twice → "name" + "name (2)"; rename a resume onto a
   sibling's name → auto-suffixed with an inline "saved as" note; renamed resume still downloads
   with its extension; profile rename with "Also rename active resume" checked syncs both.
10. **AI suggest**: Interests → Edit → "Suggest with AI" → dashed chips appear; clicking adds without
   touching existing entries; already-present values never suggested.
11. **New-visit pill**: after an ingest adds jobs, reopening For You shows "N new since last visit";
   clicking filters to only those.
12. **Profile switcher**: clicking the name caret opens the menu ATTACHED directly under the profile
   name (not floating below the whole card); works with long names and narrow widths.
