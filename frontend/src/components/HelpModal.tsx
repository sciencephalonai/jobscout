// "How JobScout works" — the one screen that explains the whole tool: the loop,
// what each tab does, what every badge means, and what the sidebar buttons do.
// Reachable from the sidebar (?) at any time; new users land here from For You.
// Single scroll inside a FIXED-height frame (Modal `tall`) so the dialog never
// resizes — one stable window, content scrolls with the thin inner scrollbar.
import type { ReactNode } from 'react'
import Modal from './ui/Modal'

function Row({ term, children }: { term: ReactNode; children: ReactNode }) {
  return (
    <div className="flex gap-2.5 py-1">
      <span className="w-32 shrink-0">{term}</span>
      <span className="min-w-0 flex-1 text-slate-600">{children}</span>
    </div>
  )
}

export default function HelpModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="How JobScout works" onClose={onClose} wide tall>
      <div className="space-y-5 text-xs leading-relaxed">
        {/* The loop */}
        <section>
          <p className="section-label mb-1.5">The loop</p>
          <div className="grid gap-2 sm:grid-cols-4">
            {[
              ['1', 'Your profile', 'Upload a resume once. It becomes your target roles, skills, experience level, and work-authorization needs — all editable.'],
              ['2', 'Sources', '20 job sources are searched with your target roles: company ATS boards (Greenhouse, Lever, Workday…), curated new-grad feeds, and aggregators.'],
              ['3', 'Screening', 'Every posting is checked against hard gates (visa, citizenship, clearance, seniority, experience, role type) and scored against your resume.'],
              ['4', 'You apply', 'For You shows what survives. Save, track, deep-match, or tailor a resume — then mark it applied.'],
            ].map(([n, title, body]) => (
              <div key={n} className="rounded-lg border border-slate-200 bg-white p-2.5">
                <span className="flex h-5 w-5 items-center justify-center rounded bg-ink font-mono text-[0.62rem] font-bold text-white">{n}</span>
                <p className="mt-1.5 font-semibold text-ink">{title}</p>
                <p className="mt-0.5 text-[0.7rem] text-slate-500">{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Tabs */}
        <section>
          <p className="section-label mb-1.5">The tabs</p>
          <Row term={<b className="text-ink">For You</b>}>
            Only roles you can realistically get: every eligibility and fit gate passed, best match first.
            Needs an active profile. Fewer results here is the point — it is a shortlist, not a search.
          </Row>
          <Row term={<b className="text-ink">Discover</b>}>
            Everything indexed, searchable and filterable. Works with <em>or without</em> a profile —
            without one you get plain search; with one you also get fit % and verdicts on each card.
          </Row>
          <Row term={<b className="text-ink">Tracker</b>}>Your saved jobs and application pipeline (Applied / OA / Interview / Offer / Rejected). Applied jobs drop out of the feed.</Row>
          <Row term={<b className="text-ink">Saved searches</b>}>Save any query + filters; the bell badges when new matches arrive.</Row>
          <Row term={<b className="text-ink">Companies</b>}>The employer watchlist that gets checked directly (their own ATS board), with H-1B and cap-exempt flags.</Row>
          <Row term={<b className="text-ink">Profile</b>}>Your resume as editable sections, plus the preferences that drive matching.</Row>
        </section>

        {/* Profiles vs resumes */}
        <section>
          <p className="section-label mb-1.5">Profiles, resumes & tailored resumes</p>
          <Row term={<b className="text-ink">Profile</b>}>
            A job-search <em>context</em>: your target roles, skills, preferences, and eligibility gates —
            plus a library of resumes. <b>Recommended: keep one profile</b> and add multiple resumes to it;
            make separate profiles only for genuinely different careers (e.g. "Software Engineer" vs
            "UX Researcher"). Each profile has its own resumes, matches, and saved jobs.
          </Row>
          <Row term={<b className="text-ink">Resume</b>}>
            An uploaded file inside a profile. Keep several; exactly <b>one is active</b>. What
            <b> matching &amp; deep-match</b> read is your <b>editable profile</b> — the active resume's text
            plus any edits or AI-polish you make in the Profile tab (they sync). Switch the active one anytime.
          </Row>
          <Row term={<b className="text-ink">Tailored resume</b>}>
            A per-job DOCX built for one specific posting from your <b>verified canonical facts</b> (not the
            editable profile), auto-selecting only the parts relevant to that job — so nothing is invented.
            Saved under the profile's "Tailored resumes", re-downloadable anytime; rename the filename inline.
          </Row>
          <Row term={<b className="text-ink">Which profile?</b>}>
            With two or more profiles, the job's detail pane picks the <em>best-fitting</em> one to tailor as
            by default (shown with each profile's fit %) — change it in the "Tailor as" dropdown without
            leaving the job, or click <b>Set active</b> next to it to make that profile your active one
            everywhere. The tailored resume saves under whichever profile you choose.
          </Row>
        </section>

        {/* Badges */}
        <section>
          <p className="section-label mb-1.5">What the badges mean</p>
          <Row term={<span className="tag bg-emerald-100 text-emerald-800">Recommended</span>}>
            Clears every gate with strong evidence — apply.
          </Row>
          <Row term={<span className="tag bg-amber-100 text-amber-800">Review</span>}>
            Qualified, but with a caveat worth checking (e.g. the posting never mentions sponsorship).
          </Row>
          <Row term={<span className="tag bg-ink text-white">Direct</span>}>
            Straight from the employer's own job board — the cleanest application link.
          </Row>
          <Row term={<span className="tag bg-slate-100 text-slate-500">Discovery</span>}>
            Found via an aggregator; the employer may have a better listing.
          </Row>
          <Row term={<span className="tag bg-signal-50 text-signal-700">Likely sponsor</span>}>
            Cap-exempt employer (university/hospital/nonprofit) or a known H-1B filer.
          </Row>
          <Row term={<span className="tag bg-amber-50 text-amber-700">Ghost risk</span>}>
            Posted long ago and still listed — may no longer be real.
          </Row>
        </section>

        {/* Buttons */}
        <section>
          <p className="section-label mb-1.5">The sidebar buttons</p>
          <Row term={<b className="text-ink">Get latest jobs</b>}>
            What the primary button does when <em>no profile is active</em>: searches every enabled source with
            generic tech keywords and indexes whatever comes back. Broad, unfiltered — good for filling Discover.
          </Row>
          <Row term={<b className="text-ink">Find profile matches</b>}>
            The same button <em>once a profile is active</em>: it searches with your target roles instead of generic
            ones and keeps only what passes your profile's eligibility gates. Run it when For You feels thin.
          </Row>
          <Row term={<b className="text-ink">Refresh watchlist</b>}>
            Re-checks only the companies you watch for newly opened (or closed) roles. Cheap and fast.
          </Row>
          <Row term={<b className="text-ink">Automation</b>}>
            Optional daily refresh, and the opt-in high-risk scraper toggle.
          </Row>
        </section>

        <p className="rounded-lg bg-slate-50 p-2.5 text-[0.7rem] text-slate-500">
          <b className="text-ink">Privacy:</b> everything (jobs, profiles, resumes) is stored locally on this
          machine. Only job text and bounded resume snippets are sent to your configured AI providers for
          enrichment and matching.
        </p>
      </div>
    </Modal>
  )
}
