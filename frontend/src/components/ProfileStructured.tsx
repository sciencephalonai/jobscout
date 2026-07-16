// Structured resume view + editors for the Profile tab.
// Copy rule: resume-CONTENT card titles use Title Case (they mirror document
// section headings, like the skill-category names themselves); product chrome
// (preferences, buttons, modals) stays sentence case — see ProfilePanel.
// Renders typed sections (Education grouped per school, Experience per role,
// Projects with tech pills, Certifications, Skills as per-category pills,
// custom sections) with master-detail edit modals, and the AI bullet-polish
// flow (per-bullet diff with accept/reject; 1 LLM call per use).
import { useState, type ReactNode } from 'react'
import {
  Briefcase, Certificate, GraduationCap, GithubLogo, Lightning, LinkSimple,
  Medal, Newspaper, PencilSimple, Plus, Rocket, Sparkle, Trash,
} from '@phosphor-icons/react'
import type {
  AchievementEntry, CertificationEntry, CustomSection, EducationEntry, ExperienceEntry,
  PolishPair, Profile, ProjectEntry, PublicationEntry, SkillCategory, StructuredResume,
} from '../types'
import { usePolishBullets } from '../api/client'
import Modal from './ui/Modal'
import TagInput from './ui/TagInput'

// ── shared bits ───────────────────────────────────────────────────────────────

function SectionShell({
  icon, title, count, onEdit, children,
}: {
  icon: ReactNode; title: string; count: string; onEdit: () => void; children: ReactNode
}) {
  return (
    <section className="workspace-surface px-4 py-3.5">
      {/* items-start + shrink-0 Edit → the title never competes for width and
          wraps (never clips) on narrow columns; the count wraps under it. */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-signal-50 text-signal-600">{icon}</span>
          <h2 className="text-sm font-semibold tracking-[-0.02em] text-ink [overflow-wrap:anywhere]">{title}</h2>
          <span className="tag bg-slate-100 font-mono text-[0.62rem] text-slate-500">{count}</span>
        </div>
        <button type="button" onClick={onEdit} className="control-focus flex h-7 shrink-0 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 text-xs font-medium text-slate-600 hover:bg-slate-50">
          <PencilSimple size={12} /> Edit
        </button>
      </div>
      <div className="mt-3 space-y-4">{children}</div>
    </section>
  )
}

/** "3 roles" / "1 role" / "0 roles" — never "1 entries". */
function count(n: number, singular: string, plural = `${singular}s`): string {
  return `${n} ${n === 1 ? singular : plural}`
}

function DateRange({ start, end, current }: { start?: string | null; end?: string | null; current?: boolean }) {
  const range = [start, current ? 'Present' : end].filter(Boolean).join(' – ')
  return range ? <span>{range}</span> : null
}

// This is the user's own resume in its home surface: show every bullet, never
// cap. Cards grow (no fixed height), so nothing is hidden or clipped.
function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="mt-1.5 space-y-1 text-[0.82rem] leading-relaxed text-slate-600">
      {items.map((b, i) => (
        <li key={i} className="flex gap-2">
          <span className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-signal-300" />
          <span className="min-w-0 [overflow-wrap:anywhere]">{b}</span>
        </li>
      ))}
    </ul>
  )
}

const inputCls = 'control-focus w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-ink'
const labelCls = 'section-label mb-1 block'

function Field({ label, value, onChange, placeholder = '' }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string
}) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={inputCls} />
    </div>
  )
}

// Bullet rows editor with optional AI polish diff.
function BulletsEditor({
  bullets, onChange, polish,
}: {
  bullets: string[]
  onChange: (next: string[]) => void
  polish?: { profileId: string; section: string; index: number }
}) {
  const polishMut = usePolishBullets()
  const [pairs, setPairs] = useState<PolishPair[] | null>(null)

  const runPolish = () => {
    if (!polish) return
    polishMut.mutate(polish, { onSuccess: (d) => setPairs(d.bullets) })
  }

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className={labelCls.replace(' mb-1', '')}>Bullets</span>
        {polish && bullets.some((b) => b.trim()) && (
          <button type="button" onClick={runPolish} disabled={polishMut.isPending}
            title="AI suggestions under strict truthfulness rules (uses 1 AI call)"
            className="control-focus flex items-center gap-1 rounded-lg border border-signal-200 bg-signal-50 px-2 py-1 text-[0.7rem] font-semibold text-signal-700 hover:bg-signal-100 disabled:opacity-50">
            <Sparkle size={12} weight="fill" /> {polishMut.isPending ? 'Improving…' : 'Improve bullets'}
          </button>
        )}
      </div>
      {polishMut.error && <p className="mb-1 text-xs text-rose-600">{polishMut.error.message}</p>}
      <div className="space-y-1.5">
        {bullets.map((b, i) => (
          <div key={i} className="flex items-start gap-1.5">
            <textarea
              value={b} rows={2}
              onChange={(e) => onChange(bullets.map((x, j) => (j === i ? e.target.value : x)))}
              className="control-focus w-full resize-y rounded-lg border border-slate-300 px-3 py-1.5 text-sm leading-snug text-ink"
            />
            <button type="button" aria-label="Remove bullet" onClick={() => onChange(bullets.filter((_, j) => j !== i))}
              className="control-focus mt-1 shrink-0 rounded-lg p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500">
              <Trash size={14} />
            </button>
          </div>
        ))}
      </div>
      <button type="button" onClick={() => onChange([...bullets, ''])}
        className="control-focus mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-500 hover:border-slate-400 hover:text-ink">
        <Plus size={13} /> Add bullet
      </button>

      {/* AI polish diff: per-bullet accept/reject */}
      {pairs && (
        <div className="mt-3 rounded-xl border border-signal-100 bg-signal-50/50 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold text-signal-800">AI suggestions — accept per bullet (facts unchanged, wording tightened)</p>
            <button type="button" onClick={() => { onChange(pairs.map((p) => p.suggested)); setPairs(null) }}
              className="control-focus rounded-lg bg-signal-600 px-2 py-1 text-[0.7rem] font-semibold text-white hover:bg-signal-700">
              Accept all
            </button>
          </div>
          <div className="space-y-2">
            {pairs.map((pair, i) => (
              <div key={i} className="rounded-lg border border-slate-200 bg-white p-2 text-xs">
                <p className="text-slate-400 line-through decoration-slate-300">{pair.original}</p>
                <p className="mt-1 font-medium text-ink">{pair.suggested}</p>
                <div className="mt-1.5 flex gap-1.5">
                  <button type="button"
                    onClick={() => { onChange(bullets.map((b) => (b === pair.original ? pair.suggested : b))); setPairs((ps) => ps && ps.filter((_, j) => j !== i)) }}
                    className="control-focus rounded border border-signal-200 bg-signal-50 px-1.5 py-0.5 font-semibold text-signal-700 hover:bg-signal-100">
                    Use suggestion
                  </button>
                  <button type="button" onClick={() => setPairs((ps) => ps && ps.filter((_, j) => j !== i))}
                    className="control-focus rounded px-1.5 py-0.5 font-medium text-slate-500 hover:bg-slate-100">
                    Keep original
                  </button>
                </div>
              </div>
            ))}
            {pairs.length === 0 && <p className="text-xs text-slate-400">All suggestions handled.</p>}
          </div>
        </div>
      )}
    </div>
  )
}

// Generic master-detail editor modal over a list of entries.
function EntriesModal<T>({
  title, entries, label, blank, renderForm, onSave, onClose, valid,
}: {
  title: string
  entries: T[]
  label: (e: T, i: number) => string
  blank: () => T
  renderForm: (entry: T, set: (next: T) => void, index: number) => ReactNode
  onSave: (next: T[]) => void
  onClose: () => void
  valid: (e: T) => boolean
}) {
  const [items, setItems] = useState<T[]>(() => (entries.length ? structuredClone(entries) : [blank()]))
  const [sel, setSel] = useState(0)
  const allValid = items.every(valid)

  return (
    <Modal
      title={title} onClose={onClose} wide
      footer={
        <>
          <button type="button" onClick={onClose} className="control-focus rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
          <button type="button" disabled={!allValid}
            onClick={() => onSave(items.filter(valid))}
            className="control-focus rounded-lg bg-ink px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-ink-soft disabled:opacity-40">
            Save changes
          </button>
        </>
      }
    >
      <div className="flex gap-4">
        <div className="w-44 shrink-0 border-r border-slate-100 pr-3">
          <div className="space-y-0.5">
            {items.map((e, i) => (
              <button key={i} type="button" onClick={() => setSel(i)} title={label(e, i) || `Item ${i + 1}`}
                className={`control-focus block w-full truncate rounded-lg px-2 py-1.5 text-left text-xs font-medium ${i === sel ? 'bg-slate-100 text-ink' : 'text-slate-500 hover:bg-slate-50'}`}>
                {label(e, i) || `Item ${i + 1}`}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => { setItems((p) => [...p, blank()]); setSel(items.length) }}
            className="control-focus mt-2 flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-500 hover:bg-slate-50 hover:text-ink">
            <Plus size={12} /> Add
          </button>
        </div>
        <div className="min-w-0 flex-1">
          {items[sel] !== undefined && (
            <>
              <div className="mb-3 flex items-center justify-between">
                <p className="text-xs font-semibold text-slate-400">Item {sel + 1} of {items.length}</p>
                <button type="button" aria-label="Delete item"
                  onClick={() => { setItems((p) => p.filter((_, j) => j !== sel)); setSel((v) => Math.max(0, v - 1)) }}
                  className="control-focus rounded-lg p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500">
                  <Trash size={14} />
                </button>
              </div>
              {renderForm(items[sel], (next) => setItems((p) => p.map((x, j) => (j === sel ? next : x))), sel)}
            </>
          )}
        </div>
      </div>
    </Modal>
  )
}

// ── the structured sections block ─────────────────────────────────────────────

type SectionKey = 'education' | 'experience' | 'projects' | 'certifications' | 'publications' | 'achievements' | 'skill_categories' | 'custom_sections'

export default function StructuredSections({
  profile, onSaveStructured,
}: {
  profile: Profile
  onSaveStructured: (next: StructuredResume) => void
}) {
  const sr = profile.structured_resume as StructuredResume
  const [editing, setEditing] = useState<SectionKey | null>(null)

  const save = (key: SectionKey, value: unknown) => {
    onSaveStructured({ ...structuredClone(sr), [key]: value })
    setEditing(null)
  }

  return (
    <>
      {/* Education */}
      <SectionShell icon={<GraduationCap size={16} weight="duotone" />} title="Education"
        count={count(sr.education.length, 'entry', 'entries')} onEdit={() => setEditing('education')}>
        {sr.education.map((e, i) => (
          <div key={i}>
            <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{e.institution}</p>
            <p className="text-xs text-slate-500">{[e.degree, e.field_of_study].filter(Boolean).join(' · ')}</p>
            <p className="mt-0.5 text-[0.7rem] text-slate-400">
              <DateRange start={e.start_date} end={e.end_date} />{e.gpa && <> · GPA {e.gpa}</>}{e.location && <> · {e.location}</>}
            </p>
            {e.honors.length > 0 && <Bullets items={e.honors} />}
          </div>
        ))}
        {sr.education.length === 0 && <p className="text-xs italic text-slate-400">No education entries.</p>}
      </SectionShell>

      {/* Experience */}
      <SectionShell icon={<Briefcase size={16} weight="duotone" />} title="Experience"
        count={count(sr.experience.length, 'role')} onEdit={() => setEditing('experience')}>
        {sr.experience.map((w, i) => (
          <div key={i}>
            <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{w.title}</p>
            <p className="text-xs text-slate-500">{w.company}{w.location && <> · {w.location}</>}</p>
            <p className="mt-0.5 text-[0.7rem] text-slate-400"><DateRange start={w.start_date} end={w.end_date} current={w.current} /></p>
            {w.summary && <p className="mt-1 text-xs text-slate-500">{w.summary}</p>}
            <Bullets items={w.bullets} />
          </div>
        ))}
        {sr.experience.length === 0 && <p className="text-xs italic text-slate-400">No experience entries.</p>}
      </SectionShell>

      {/* Projects */}
      <SectionShell icon={<Rocket size={16} weight="duotone" />} title="Projects"
        count={count(sr.projects.length, 'project')} onEdit={() => setEditing('projects')}>
        {sr.projects.map((p, i) => (
          <div key={i}>
            <div className="flex flex-wrap items-center gap-1.5">
              <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{p.name}</p>
              {p.github_url && <a href={p.github_url} target="_blank" rel="noreferrer" className="tag control-focus bg-slate-100 text-slate-600 hover:text-ink"><GithubLogo size={11} /> GitHub</a>}
              {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="tag control-focus bg-emerald-50 text-emerald-700"><LinkSimple size={11} /> Live</a>}
            </div>
            {p.technologies.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {p.technologies.map((t) => <span key={t} className="tag bg-signal-50 text-[0.66rem] text-signal-800">{t}</span>)}
              </div>
            )}
            <Bullets items={p.bullets} />
          </div>
        ))}
        {sr.projects.length === 0 && <p className="text-xs italic text-slate-400">No projects.</p>}
      </SectionShell>

      {/* Publications */}
      <SectionShell icon={<Newspaper size={16} weight="duotone" />} title="Publications"
        count={count((sr.publications ?? []).length, 'paper')} onEdit={() => setEditing('publications')}>
        {(sr.publications ?? []).map((pub, i) => (
          <div key={i}>
            <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">
              {pub.url ? <a href={pub.url} target="_blank" rel="noreferrer" className="control-focus rounded hover:text-signal-700">{pub.title}</a> : pub.title}
            </p>
            <p className="text-xs text-slate-500">{[pub.venue, pub.date].filter(Boolean).join(' · ')}</p>
            {pub.authors.length > 0 && <p className="mt-0.5 text-[0.7rem] text-slate-400">{pub.authors.join(', ')}</p>}
            {pub.description && <p className="mt-1 text-[0.82rem] leading-relaxed text-slate-600">{pub.description}</p>}
            {pub.url && <a href={pub.url} target="_blank" rel="noreferrer" className="tag mt-1 inline-flex bg-slate-100 text-[0.66rem] text-slate-600 hover:text-ink"><LinkSimple size={11} /> DOI</a>}
          </div>
        ))}
        {(sr.publications ?? []).length === 0 && <p className="text-xs italic text-slate-400">No papers or articles.</p>}
      </SectionShell>

      {/* Achievements */}
      <SectionShell icon={<Medal size={16} weight="duotone" />} title="Achievements & Awards"
        count={count((sr.achievements ?? []).length, 'award')} onEdit={() => setEditing('achievements')}>
        {(sr.achievements ?? []).map((a, i) => (
          <div key={i}>
            <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{a.title}</p>
            <p className="text-xs text-slate-500">{[a.issuer, a.date].filter(Boolean).join(' · ')}</p>
            {a.description && <p className="mt-1 text-[0.82rem] leading-relaxed text-slate-600">{a.description}</p>}
          </div>
        ))}
        {(sr.achievements ?? []).length === 0 && <p className="text-xs italic text-slate-400">No awards or honors.</p>}
      </SectionShell>

      {/* Certifications */}
      <SectionShell icon={<Certificate size={16} weight="duotone" />} title="Certifications"
        count={count(sr.certifications.length, 'credential')} onEdit={() => setEditing('certifications')}>
        {sr.certifications.map((c, i) => (
          <div key={i}>
            <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{c.url ? <a href={c.url} target="_blank" rel="noreferrer" className="control-focus rounded hover:text-signal-700">{c.name}</a> : c.name}</p>
            <p className="text-xs text-slate-500">{[c.issuer, c.date].filter(Boolean).join(' · ')}</p>
          </div>
        ))}
        {sr.certifications.length === 0 && <p className="text-xs italic text-slate-400">No licenses or course credentials.</p>}
      </SectionShell>

      {/* Custom sections */}
      <SectionShell icon={<Lightning size={16} weight="duotone" />} title="Custom Sections"
        count={count(sr.custom_sections.length, 'section')} onEdit={() => setEditing('custom_sections')}>
        {sr.custom_sections.map((c, i) => (
          <div key={i}>
            <p className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{c.title}</p>
            <Bullets items={c.bullets} />
          </div>
        ))}
        {sr.custom_sections.length === 0 && <p className="text-xs italic text-slate-400">Add anything else worth showing (volunteering, awards, languages…).</p>}
      </SectionShell>

      {/* ── editors ── */}
      {editing === 'education' && (
        <EntriesModal<EducationEntry>
          title="Edit education" entries={sr.education} onClose={() => setEditing(null)}
          label={(e) => e.institution} valid={(e) => !!e.institution.trim()}
          blank={() => ({ institution: '', degree: null, field_of_study: null, gpa: null, start_date: null, end_date: null, location: null, honors: [] })}
          onSave={(next) => save('education', next)}
          renderForm={(e, set) => (
            <div className="space-y-3">
              <Field label="Institution *" value={e.institution} onChange={(v) => set({ ...e, institution: v })} />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Degree" value={e.degree ?? ''} onChange={(v) => set({ ...e, degree: v || null })} placeholder="Master of Science (M.Sc.)" />
                <Field label="Field of study" value={e.field_of_study ?? ''} onChange={(v) => set({ ...e, field_of_study: v || null })} placeholder="Data Science" />
                <Field label="GPA" value={e.gpa ?? ''} onChange={(v) => set({ ...e, gpa: v || null })} placeholder="3.86/4.0" />
                <Field label="Location" value={e.location ?? ''} onChange={(v) => set({ ...e, location: v || null })} />
                <Field label="Start" value={e.start_date ?? ''} onChange={(v) => set({ ...e, start_date: v || null })} placeholder="Aug 2024" />
                <Field label="End (expected)" value={e.end_date ?? ''} onChange={(v) => set({ ...e, end_date: v || null })} placeholder="May 2026" />
              </div>
            </div>
          )}
        />
      )}
      {editing === 'experience' && (
        <EntriesModal<ExperienceEntry>
          title="Edit experience" entries={sr.experience} onClose={() => setEditing(null)}
          label={(e) => e.company || e.title} valid={(e) => !!e.company.trim() && !!e.title.trim()}
          blank={() => ({ company: '', title: '', location: null, start_date: null, end_date: null, current: false, summary: null, bullets: [''] })}
          onSave={(next) => save('experience', next)}
          renderForm={(e, set, idx) => (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Company *" value={e.company} onChange={(v) => set({ ...e, company: v })} />
                <Field label="Role *" value={e.title} onChange={(v) => set({ ...e, title: v })} />
                <Field label="Location" value={e.location ?? ''} onChange={(v) => set({ ...e, location: v || null })} />
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Start" value={e.start_date ?? ''} onChange={(v) => set({ ...e, start_date: v || null })} placeholder="Jun 2025" />
                  <Field label="End" value={e.end_date ?? ''} onChange={(v) => set({ ...e, end_date: v || null })} placeholder="Aug 2025" />
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input type="checkbox" checked={e.current} onChange={(ev) => set({ ...e, current: ev.target.checked })} className="h-4 w-4 accent-signal-500" />
                Currently working here
              </label>
              <BulletsEditor bullets={e.bullets} onChange={(b) => set({ ...e, bullets: b })}
                polish={{ profileId: profile.id, section: 'experience', index: idx }} />
            </div>
          )}
        />
      )}
      {editing === 'projects' && (
        <EntriesModal<ProjectEntry>
          title="Edit projects" entries={sr.projects} onClose={() => setEditing(null)}
          label={(e) => e.name} valid={(e) => !!e.name.trim()}
          blank={() => ({ name: '', technologies: [], url: null, github_url: null, start_date: null, end_date: null, bullets: [''] })}
          onSave={(next) => save('projects', next)}
          renderForm={(e, set, idx) => (
            <div className="space-y-3">
              <Field label="Title *" value={e.name} onChange={(v) => set({ ...e, name: v })} />
              <div>
                <label className={labelCls}>Technologies</label>
                <TagInput values={e.technologies} onChange={(t) => set({ ...e, technologies: t })} placeholder="Python, Docker, …" accent />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="GitHub URL" value={e.github_url ?? ''} onChange={(v) => set({ ...e, github_url: v || null })} placeholder="https://github.com/…" />
                <Field label="Live demo URL" value={e.url ?? ''} onChange={(v) => set({ ...e, url: v || null })} placeholder="https://…" />
              </div>
              <BulletsEditor bullets={e.bullets} onChange={(b) => set({ ...e, bullets: b })}
                polish={{ profileId: profile.id, section: 'projects', index: idx }} />
            </div>
          )}
        />
      )}
      {editing === 'certifications' && (
        <EntriesModal<CertificationEntry>
          title="Edit certifications" entries={sr.certifications} onClose={() => setEditing(null)}
          label={(e) => e.name} valid={(e) => !!e.name.trim()}
          blank={() => ({ name: '', issuer: null, date: null, credential_id: null, url: null })}
          onSave={(next) => save('certifications', next)}
          renderForm={(e, set) => (
            <div className="space-y-3">
              <Field label="Name *" value={e.name} onChange={(v) => set({ ...e, name: v })} />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Issuing organization" value={e.issuer ?? ''} onChange={(v) => set({ ...e, issuer: v || null })} />
                <Field label="Date" value={e.date ?? ''} onChange={(v) => set({ ...e, date: v || null })} />
                <Field label="Credential ID" value={e.credential_id ?? ''} onChange={(v) => set({ ...e, credential_id: v || null })} />
                <Field label="Credential URL" value={e.url ?? ''} onChange={(v) => set({ ...e, url: v || null })} placeholder="https://…" />
              </div>
            </div>
          )}
        />
      )}
      {editing === 'publications' && (
        <EntriesModal<PublicationEntry>
          title="Edit publications" entries={sr.publications ?? []} onClose={() => setEditing(null)}
          label={(e) => e.title} valid={(e) => !!e.title.trim()}
          blank={() => ({ title: '', venue: null, date: null, url: null, authors: [], description: null })}
          onSave={(next) => save('publications', next)}
          renderForm={(e, set) => (
            <div className="space-y-3">
              <Field label="Title *" value={e.title} onChange={(v) => set({ ...e, title: v })} />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Venue (conference / journal)" value={e.venue ?? ''} onChange={(v) => set({ ...e, venue: v || null })} placeholder="ICSTE-23" />
                <Field label="Date" value={e.date ?? ''} onChange={(v) => set({ ...e, date: v || null })} placeholder="2023" />
              </div>
              <Field label="DOI / URL" value={e.url ?? ''} onChange={(v) => set({ ...e, url: v || null })} placeholder="https://doi.org/…" />
              <div>
                <label className={labelCls}>Authors</label>
                <TagInput values={e.authors} onChange={(a) => set({ ...e, authors: a })} placeholder="Add co-authors" />
              </div>
              <div>
                <label className={labelCls}>Description</label>
                <textarea value={e.description ?? ''} rows={3} onChange={(ev) => set({ ...e, description: ev.target.value || null })}
                  className="control-focus w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm leading-snug text-ink" />
              </div>
            </div>
          )}
        />
      )}
      {editing === 'achievements' && (
        <EntriesModal<AchievementEntry>
          title="Edit achievements" entries={sr.achievements ?? []} onClose={() => setEditing(null)}
          label={(e) => e.title} valid={(e) => !!e.title.trim()}
          blank={() => ({ title: '', issuer: null, date: null, description: null })}
          onSave={(next) => save('achievements', next)}
          renderForm={(e, set) => (
            <div className="space-y-3">
              <Field label="Title *" value={e.title} onChange={(v) => set({ ...e, title: v })} placeholder="Dean's List / Hackathon winner / Scholarship" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Issuer / context" value={e.issuer ?? ''} onChange={(v) => set({ ...e, issuer: v || null })} />
                <Field label="Date" value={e.date ?? ''} onChange={(v) => set({ ...e, date: v || null })} />
              </div>
              <div>
                <label className={labelCls}>Description</label>
                <textarea value={e.description ?? ''} rows={3} onChange={(ev) => set({ ...e, description: ev.target.value || null })}
                  className="control-focus w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm leading-snug text-ink" />
              </div>
            </div>
          )}
        />
      )}
      {editing === 'custom_sections' && (
        <EntriesModal<CustomSection>
          title="Edit custom sections" entries={sr.custom_sections} onClose={() => setEditing(null)}
          label={(e) => e.title} valid={(e) => !!e.title.trim()}
          blank={() => ({ title: '', bullets: [''] })}
          onSave={(next) => save('custom_sections', next)}
          renderForm={(e, set) => (
            <div className="space-y-3">
              <Field label="Section title *" value={e.title} onChange={(v) => set({ ...e, title: v })} placeholder="e.g., Volunteer Experience, Languages" />
              <BulletsEditor bullets={e.bullets} onChange={(b) => set({ ...e, bullets: b })} />
            </div>
          )}
        />
      )}
    </>
  )
}

// ── Skills (per-category pills) — exported separately for the right column ────

export function SkillCategoriesCard({
  profile, onSave,
}: {
  profile: Profile
  onSave: (categories: SkillCategory[], flatSkills: string[]) => void
}) {
  const cats = profile.structured_resume?.skill_categories ?? []
  const [open, setOpen] = useState(false)
  const total = cats.reduce((n, c) => n + c.skills.length, 0)

  return (
    <section className="workspace-surface px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500"><Lightning size={16} weight="duotone" /></span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-[-0.02em] text-ink">Skills</h2>
            <p className="truncate text-[0.68rem] text-slate-400">{count(total, 'skill')} across {count(cats.length, 'category', 'categories')}</p>
          </div>
        </div>
        <button type="button" onClick={() => setOpen(true)} className="control-focus flex h-7 shrink-0 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 text-xs font-medium text-slate-600 hover:bg-slate-50">
          <PencilSimple size={12} /> Edit
        </button>
      </div>
      <div className="mt-2.5 space-y-2.5">
        {cats.map((c) => (
          <div key={c.name}>
            <p className="section-label mb-1">{c.name}</p>
            <div className="flex flex-wrap gap-1">
              {c.skills.map((s) => <span key={s} className="tag bg-signal-50 text-[0.68rem] text-signal-800">{s}</span>)}
            </div>
          </div>
        ))}
        {cats.length === 0 && <p className="text-xs italic text-slate-400">No skill categories — Edit to add.</p>}
      </div>

      {open && (
        <EntriesModal<SkillCategory>
          title="Edit skills" entries={cats} onClose={() => setOpen(false)}
          label={(c) => c.name} valid={(c) => !!c.name.trim()}
          blank={() => ({ name: '', skills: [] })}
          onSave={(next) => {
            const flat = [...new Set(next.flatMap((c) => c.skills.map((s) => s.toLowerCase())))]
            onSave(next, flat)
            setOpen(false)
          }}
          renderForm={(c, set) => (
            <div className="space-y-3">
              <Field label="Category name *" value={c.name} onChange={(v) => set({ ...c, name: v })} placeholder="Programming Languages" />
              <div>
                <label className={labelCls}>Skills</label>
                <TagInput values={c.skills} onChange={(sk) => set({ ...c, skills: sk })} accent placeholder="Python, SQL, …" />
              </div>
            </div>
          )}
        />
      )}
    </section>
  )
}
