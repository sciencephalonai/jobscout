// Profile tab: sectioned, fully-editable candidate profile.
// Layout: identity header (profile switcher, completeness, resume chip) →
// left column = resume sections as cards (edit / add custom / delete) →
// right column = the matching preferences that actually drive For You
// (targets, skills, interests, eligibility toggles, work prefs, deep-match
// steering, excluded companies). Every edit opens a Modal in the app shell
// style; list fields use TagInput; booleans use ToggleRow.
import { useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ArrowCounterClockwise,
  Briefcase,
  CaretDown,
  Check,
  Certificate,
  DotsSixVertical,
  DownloadSimple,
  FileText,
  GraduationCap,
  Lightning,
  PencilSimple,
  Plus,
  Rocket,
  Sliders,
  Sparkle,
  TextT,
  Trash,
  UserCircle,
} from '@phosphor-icons/react'
import type { Profile, ResumeSection } from '../types'
import {
  useAttachProfileResume,
  useStructureProfile,
  useDeleteProfileById,
  useProfiles,
  useRenameResume,
  useReparseProfile,
  useResumes,
  useSuggestField,
  useUpdateProfile,
} from '../api/client'
import { useActiveProfile } from '../ProfileContext'
import { ResumeDropMatch } from './ResumeMatchPanel'
import ResumeLibrary from './ResumeLibrary'
import StructuredSections, { SkillCategoriesCard } from './ProfileStructured'
import Modal from './ui/Modal'
import TagInput from './ui/TagInput'
import ToggleRow from './ui/ToggleRow'

const SENIORITY_OPTIONS = [
  'intern', 'junior', 'mid', 'senior', 'staff', 'principal', 'lead',
  'manager', 'director', 'vp', 'c_level',
]

// ── helpers ──────────────────────────────────────────────────────────────────

function sectionIcon(heading: string) {
  const h = heading.toLowerCase()
  if (h.includes('education')) return <GraduationCap size={16} weight="duotone" />
  if (h.includes('experience') || h.includes('employment')) return <Briefcase size={16} weight="duotone" />
  if (h.includes('project')) return <Rocket size={16} weight="duotone" />
  if (h.includes('cert') || h.includes('publication')) return <Certificate size={16} weight="duotone" />
  if (h.includes('skill')) return <Lightning size={16} weight="duotone" />
  return <FileText size={16} weight="duotone" />
}

function contentLines(content: string): string[] {
  return content
    .split('\n')
    .map((l) => l.replace(/^[-•*]\s*/, '').trim())
    .filter(Boolean)
}

function completeness(p: Profile): { pct: number; missing: string[] } {
  const checks: Array<[string, boolean]> = [
    ['resume', !!p.resume_text],
    ['target roles', p.target_titles.length > 0],
    ['skills', p.skills.length >= 3],
    ['seniority & experience', !!p.seniority_max],
    ['interests', p.interests.length > 0],
  ]
  const done = checks.filter(([, ok]) => ok).length
  return {
    pct: Math.round((done / checks.length) * 100),
    missing: checks.filter(([, ok]) => !ok).map(([name]) => name),
  }
}

// ── section edit modal (heading + bullet rows, add/remove/reorder) ───────────

function SectionEditModal({
  initial,
  title,
  onSave,
  onClose,
}: {
  initial: ResumeSection
  title: string
  onSave: (next: ResumeSection) => void
  onClose: () => void
}) {
  const [heading, setHeading] = useState(initial.heading)
  const [lines, setLines] = useState<string[]>(() => {
    const l = contentLines(initial.content)
    return l.length ? l : ['']
  })
  const [dragIdx, setDragIdx] = useState<number | null>(null)

  const valid = heading.trim().length > 0 && lines.some((l) => l.trim())
  const setLine = (i: number, v: string) => setLines((prev) => prev.map((l, j) => (j === i ? v : l)))

  return (
    <Modal
      title={title}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" onClick={onClose} className="control-focus rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button
            type="button"
            disabled={!valid}
            onClick={() =>
              onSave({
                heading: heading.trim(),
                content: lines.map((l) => l.trim()).filter(Boolean).map((l) => `- ${l}`).join('\n'),
              })
            }
            className="control-focus rounded-lg bg-ink px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-ink-soft disabled:cursor-not-allowed disabled:opacity-40"
          >
            Save changes
          </button>
        </>
      }
    >
      <label className="section-label mb-1 block">Section title *</label>
      <input
        value={heading}
        onChange={(e) => setHeading(e.target.value)}
        placeholder="e.g., Volunteer Experience, Publications"
        className="control-focus mb-4 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-ink"
      />
      <p className="section-label mb-1">Items</p>
      <div className="space-y-1.5">
        {lines.map((line, i) => (
          <div
            key={i}
            draggable
            onDragStart={() => setDragIdx(i)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => {
              if (dragIdx === null || dragIdx === i) return
              setLines((prev) => {
                const next = [...prev]
                const [moved] = next.splice(dragIdx, 1)
                next.splice(i, 0, moved)
                return next
              })
              setDragIdx(null)
            }}
            className="flex items-center gap-1.5"
          >
            <DotsSixVertical size={14} className="shrink-0 cursor-grab text-slate-300" />
            <input
              value={line}
              onChange={(e) => setLine(i, e.target.value)}
              placeholder="One accomplishment / detail per line"
              className="control-focus w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-ink"
            />
            <button
              type="button"
              aria-label="Remove item"
              onClick={() => setLines((prev) => prev.filter((_, j) => j !== i))}
              className="control-focus shrink-0 rounded-lg p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500"
            >
              <Trash size={14} />
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => setLines((prev) => [...prev, ''])}
        className="control-focus mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs font-medium text-slate-500 hover:border-slate-400 hover:text-ink"
      >
        <Plus size={13} /> Add item
      </button>
    </Modal>
  )
}

// ── generic “edit one field group” modal for the right-column cards ──────────

function PrefsModalShell({
  title,
  onClose,
  onSave,
  children,
  saveDisabled = false,
}: {
  title: string
  onClose: () => void
  onSave: () => void
  children: ReactNode
  saveDisabled?: boolean
}) {
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} className="control-focus rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button type="button" disabled={saveDisabled} onClick={onSave} className="control-focus rounded-lg bg-ink px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-ink-soft disabled:opacity-40">
            Save changes
          </button>
        </>
      }
    >
      {children}
    </Modal>
  )
}

// ── AI suggestions for list fields (ADD-ONLY; existing values never touched) ──

const SUGGESTIBLE_FIELDS = ['interests', 'avoid_role_types', 'avoid_domains', 'target_titles', 'skills']

function SuggestChips({
  profileId, field, current, onAdd,
}: {
  profileId: string; field: string; current: string[]; onAdd: (items: string[]) => void
}) {
  const suggest = useSuggestField()
  // Hide chips already accepted into the draft.
  const pending = (suggest.data?.suggestions ?? []).filter(
    (s) => !current.some((c) => c.toLowerCase() === s.toLowerCase()),
  )
  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <div className="flex items-center justify-between">
        <button type="button" onClick={() => suggest.mutate({ profileId, field })} disabled={suggest.isPending}
          title="AI suggestions grounded in your resume — additions only, nothing is removed (uses 1 AI call)"
          className="control-focus flex items-center gap-1 rounded-lg border border-signal-200 bg-signal-50 px-2 py-1 text-[0.7rem] font-semibold text-signal-700 hover:bg-signal-100 disabled:opacity-50">
          <Sparkle size={12} weight="fill" /> {suggest.isPending ? 'Thinking…' : 'Suggest with AI (1 call)'}
        </button>
        {pending.length > 1 && (
          <button type="button" onClick={() => onAdd(pending)}
            className="control-focus rounded px-1.5 py-0.5 text-[0.7rem] font-medium text-slate-500 hover:bg-slate-100">
            Add all
          </button>
        )}
      </div>
      {suggest.error && <p className="mt-1.5 text-xs text-rose-600">{suggest.error.message}</p>}
      {suggest.data && pending.length === 0 && !suggest.isPending && (
        <p className="mt-1.5 text-xs text-slate-400">No new suggestions — everything relevant is already listed.</p>
      )}
      {pending.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {pending.map((s) => (
            <button key={s} type="button" onClick={() => onAdd([s])}
              title="Click to add"
              className="control-focus tag border border-dashed border-signal-300 bg-white text-signal-700 hover:bg-signal-50">
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── main page ────────────────────────────────────────────────────────────────

type TagField =
  | 'target_titles' | 'skills' | 'interests'
  | 'avoid_role_types' | 'avoid_domains' | 'excluded_companies'

type EditTarget =
  | { kind: 'section'; index: number }
  | { kind: 'new-section' }
  | { kind: 'tags'; field: TagField; title: string; hint: string }
  | { kind: 'eligibility' }
  | { kind: 'work-prefs' }
  | { kind: 'raw-text' }
  | null

export default function ProfilePanel() {
  const { data: profiles } = useProfiles()
  const { activeProfileId, setActiveProfileId } = useActiveProfile()
  const update = useUpdateProfile()
  const reparse = useReparseProfile()
  const remove = useDeleteProfileById()
  const attach = useAttachProfileResume()
  const structure = useStructureProfile()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [edit, setEdit] = useState<EditTarget>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [switcherOpen, setSwitcherOpen] = useState(false)
  const [editingLabel, setEditingLabel] = useState(false)
  const [labelDraft, setLabelDraft] = useState('')
  // Enter fires commit, then the input unmounts and blur fires commit AGAIN —
  // this ref makes each edit session commit exactly once.
  const labelCommittedRef = useRef(false)
  // Default-on: renaming the profile also renames the active resume to match.
  const [syncResumeName, setSyncResumeName] = useState(true)
  const renameResume = useRenameResume()

  const profile = useMemo(() => {
    const list = profiles ?? []
    return list.find((p) => p.id === (selectedId ?? activeProfileId)) ?? list[0] ?? null
  }, [profiles, selectedId, activeProfileId])

  const { data: resumeLib } = useResumes(profile?.id ?? null)

  // Draft state for the currently open modal (copy-on-open, save-on-confirm).
  const [draftTags, setDraftTags] = useState<string[]>([])
  const [draftText, setDraftText] = useState('')
  const [draftBools, setDraftBools] = useState<Record<string, boolean>>({})
  const [draftWork, setDraftWork] = useState<{
    remote: Profile['remote_preference']; seniority: string; yoe: number; capExempt: boolean
  }>({ remote: 'any', seniority: 'junior', yoe: 2, capExempt: false })

  if (!profile) {
    return (
      <div className="page-shell">
        <header className="page-header">
          <div>
            <h1 className="page-title">Candidate profile</h1>
            <p className="page-description">Drop a resume to create your first profile — everything else builds from it.</p>
          </div>
        </header>
        <ResumeDropMatch />
      </div>
    )
  }

  const { pct, missing } = completeness(profile)
  const isActive = profile.id === activeProfileId

  const save = (mutator: (p: Profile) => Profile) => {
    const clone: Profile = JSON.parse(JSON.stringify(profile))
    update.mutate(mutator(clone))
    setEdit(null)
  }

  // Rename the profile. The sidebar selector + switcher read the same profiles
  // query, so they update automatically once useUpdateProfile invalidates it.
  const commitLabel = () => {
    if (labelCommittedRef.current) return // Enter already committed; ignore the trailing blur
    labelCommittedRef.current = true
    const next = labelDraft.trim()
    setEditingLabel(false)
    if (!next || next === profile.label) return
    // Pin the view to THIS profile: the list sorts by label, so a rename re-sorts
    // it and the unpinned `?? list[0]` fallback could flip to a look-alike profile
    // (the "my rename didn't change anything" bug with duplicate labels).
    setSelectedId(profile.id)
    const clone: Profile = JSON.parse(JSON.stringify(profile))
    clone.label = next
    update.mutate(clone)
    // Owner-requested sync: keep the active resume's display name matching the
    // profile name (extension preserved). Opt-out via the checkbox.
    if (syncResumeName && profile.active_resume_id) {
      const row = resumeLib?.resumes.find((r) => r.id === profile.active_resume_id)
      const suffix = row?.filename.match(/\.[A-Za-z0-9]{1,8}$/)?.[0] ?? ''
      renameResume.mutate({
        profileId: profile.id,
        resumeId: profile.active_resume_id,
        filename: `${next}${suffix}`,
      })
    }
  }

  const openTags = (field: TagField, title: string, hint: string) => {
    setDraftTags([...((profile[field] as string[] | undefined) ?? [])])
    setEdit({ kind: 'tags', field, title, hint })
  }

  return (
    <div className="page-shell">
      <div className="mx-auto w-full max-w-6xl space-y-3">
        <header className="page-header">
          <div>
            <h1 className="page-title">Candidate profile</h1>
            <p className="page-description">
              Everything here drives matching: For You reads the preferences, semantic search reads the resume sections.
            </p>
          </div>
        </header>

        {/* ── Identity header ── */}
        <section className="workspace-surface relative px-4 py-3.5">
          {/* Row 1 — identity (name length only ever affects this block) */}
          <div className="flex items-start gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-ink text-sm font-bold tracking-tight text-white">
              {profile.label.slice(0, 2).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                {editingLabel ? (
                  <>
                    <input
                      autoFocus
                      value={labelDraft}
                      onChange={(e) => setLabelDraft(e.target.value)}
                      onBlur={commitLabel}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitLabel()
                        if (e.key === 'Escape') { labelCommittedRef.current = true; setLabelDraft(profile.label); setEditingLabel(false) }
                      }}
                      className="control-focus min-w-0 rounded-lg border border-slate-300 px-2 py-0.5 text-[0.95rem] font-semibold tracking-[-0.02em] text-ink"
                    />
                    {profile.active_resume_id && (
                      <label className="flex shrink-0 items-center gap-1 text-[0.68rem] text-slate-500" onMouseDown={(e) => e.preventDefault()}>
                        <input
                          type="checkbox"
                          checked={syncResumeName}
                          onChange={(e) => setSyncResumeName(e.target.checked)}
                          className="h-3 w-3 accent-signal-500"
                        />
                        Also rename active resume
                      </label>
                    )}
                  </>
                ) : (
                  <>
                    {/* The name IS the dropdown trigger: a subtle control (hover/open
                        background + caret) with the menu anchored to it, so the menu
                        reads as the trigger's extension — not a box floating over the card. */}
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => setSwitcherOpen((v) => !v)}
                        aria-haspopup="listbox"
                        aria-expanded={switcherOpen}
                        className={`control-focus flex max-w-full items-center gap-1.5 rounded-lg border px-2 py-1 text-[0.95rem] font-semibold tracking-[-0.02em] text-ink transition-colors ${switcherOpen ? 'border-slate-200 bg-slate-100' : 'border-transparent hover:bg-slate-100'}`}
                        title="Switch profile"
                      >
                        <span className="min-w-0 truncate">{profile.label}</span>
                        <CaretDown size={13} className={`shrink-0 text-slate-400 transition-transform ${switcherOpen ? 'rotate-180' : ''}`} />
                      </button>
                      {switcherOpen && (
                        <button type="button" aria-label="Close profile switcher" onClick={() => setSwitcherOpen(false)}
                          className="fixed inset-0 z-10 cursor-default" />
                      )}
                      {switcherOpen && (
                        <div className="popover-enter absolute left-0 top-full z-20 mt-1 min-w-full w-72 max-w-[calc(100vw-3rem)] rounded-xl border border-slate-200 bg-white p-1 shadow-xl">
                          <p className="section-label px-2.5 pb-1 pt-1.5">Switch profile</p>
                          {(profiles ?? []).map((p) => {
                            const isCurrent = p.id === profile.id
                            return (
                              <div key={p.id} className={`flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm ${isCurrent ? 'bg-signal-50/70' : 'hover:bg-slate-50'}`}>
                                <button
                                  type="button"
                                  title={p.label}
                                  className="control-focus flex min-w-0 flex-1 items-center gap-2 rounded text-left"
                                  onClick={() => { setSelectedId(p.id); setSwitcherOpen(false) }}
                                >
                                  <Check size={13} weight="bold" className={`shrink-0 ${isCurrent ? 'text-signal-600' : 'text-transparent'}`} />
                                  <span className="min-w-0 truncate font-medium text-ink">{p.label}</span>
                                  {p.id === activeProfileId && (
                                    <span className="tag shrink-0 bg-signal-50 text-[0.6rem] font-semibold text-signal-700">Active</span>
                                  )}
                                </button>
                                <button
                                  type="button"
                                  aria-label={`Delete ${p.label}`}
                                  onClick={() => { if (window.confirm(`Delete profile "${p.label}"? This also removes its saved/applied job states.`)) remove.mutate(p.id) }}
                                  className="control-focus shrink-0 rounded p-1 text-slate-300 hover:bg-rose-50 hover:text-rose-500"
                                >
                                  <Trash size={13} />
                                </button>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      aria-label="Rename profile"
                      title="Rename this profile"
                      onClick={() => { labelCommittedRef.current = false; setLabelDraft(profile.label); setEditingLabel(true) }}
                      className="control-focus rounded p-1 text-slate-300 hover:bg-slate-100 hover:text-slate-600"
                    >
                      <PencilSimple size={13} />
                    </button>
                  </>
                )}
                {isActive ? (
                  <span className="tag bg-signal-50 font-semibold text-signal-700">Active for matching</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => setActiveProfileId(profile.id)}
                    className="tag control-focus border border-slate-300 bg-white text-slate-600 hover:border-signal-300 hover:text-signal-700"
                  >
                    Set active
                  </button>
                )}
              </div>
              {(update.error || renameResume.error) && (
                <p className="mt-0.5 text-xs text-rose-600">
                  Save failed: {(update.error ?? renameResume.error)?.message}
                </p>
              )}
              {update.data && update.variables && update.data.label !== update.variables.label && (
                <p className="mt-0.5 text-xs text-amber-600">
                  Name taken — saved as “{update.data.label}” (rename anytime)
                </p>
              )}
              <p className="mt-0.5 truncate text-xs text-slate-400" title={profile.resume_filename ? `Resume: ${profile.resume_filename}` : undefined}>
                {profile.resume_filename
                  ? <>Resume: {profile.resume_filename}{profile.resume_uploaded_at && <> · uploaded {new Date(profile.resume_uploaded_at).toLocaleDateString()}</>}</>
                  : 'No source resume attached'}
              </p>
            </div>
          </div>

          {/* Row 2 — tools (completeness left, actions right; never moves with the name) */}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
            <div className="w-full sm:w-56">
              <div className="flex items-center justify-between text-[0.68rem] font-medium text-slate-500">
                <span>Completeness</span><span className="font-mono">{pct}%</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full rounded-full bg-signal-500 transition-all" style={{ width: `${pct}%` }} />
              </div>
              {missing.length > 0 && (
                <p className="mt-1 truncate text-[0.65rem] text-slate-400" title={missing.join(', ')}>
                  Missing: {missing.join(', ')}
                </p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {profile.resume_filename && (
                <a
                  href={`/api/profiles/${profile.id}/resume`}
                  className="control-focus flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                  title="Download the original resume file"
                >
                  <DownloadSimple size={14} /> Resume
                </a>
              )}
              <button
                type="button"
                onClick={() => { setDraftText(profile.resume_text ?? ''); setEdit({ kind: 'raw-text' }) }}
                className="control-focus flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                title="View or bulk-edit the flat resume text (sections re-derive on save)"
              >
                <TextT size={14} /> Raw text
              </button>
              <button
                type="button"
                onClick={() => reparse.mutate(profile.id)}
                disabled={reparse.isPending || !profile.resume_text}
                className="control-focus relative flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                title={profile.structured_stale && profile.structured_resume
                  ? 'Resume text changed — rebuild to refresh the structured view'
                  : 'Re-extract targets/skills/sections from the saved resume text'}
              >
                <ArrowCounterClockwise size={14} /> {reparse.isPending ? 'Rebuilding…' : 'Rebuild'}
                {profile.structured_stale && profile.structured_resume && !reparse.isPending && (
                  <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-amber-500 ring-2 ring-white" aria-label="Structured view out of date" />
                )}
              </button>
              <button
                type="button"
                onClick={() => setShowUpload((v) => !v)}
                className="control-focus flex h-8 items-center gap-1.5 rounded-lg bg-ink px-2.5 text-xs font-semibold text-white hover:bg-ink-soft"
                title="Create a separate profile from a resume (add resumes to THIS profile in the Resumes card below)"
              >
                <Plus size={14} /> {showUpload ? 'Hide upload' : 'New profile from resume'}
              </button>
            </div>
          </div>

        </section>

        {/* Metadata-only profile: offer one-click resume attach from a sibling. */}
        {!profile.resume_text && (() => {
          const donor = (profiles ?? []).find((p) => p.id !== profile.id && p.resume_text)
          return donor ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
              <span>This profile has no resume text — matching runs without semantic evidence.</span>
              <button
                type="button"
                disabled={attach.isPending}
                onClick={() => attach.mutate({ profileId: profile.id, sourceProfileId: donor.id })}
                className="control-focus rounded-lg border border-amber-300 bg-white px-2.5 py-1 font-semibold text-amber-800 hover:bg-amber-100 disabled:opacity-50"
              >
                {attach.isPending ? 'Attaching…' : `Attach resume from “${donor.label}”`}
              </button>
            </div>
          ) : null
        })()}

        {showUpload && <ResumeDropMatch />}

        <ResumeLibrary profileId={profile.id} />

        <div className="grid gap-3 lg:grid-cols-[1fr_minmax(20rem,26rem)]">
          {/* ── Left: resume sections (structured when parsed; flat fallback) ── */}
          <div className="min-w-0 space-y-3">
            {profile.structured_resume ? (
              <StructuredSections
                profile={profile}
                onSaveStructured={(next) => save((p) => ({ ...p, structured_resume: next }) as Profile)}
              />
            ) : (
              <>
            {profile.resume_text && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-signal-100 bg-signal-50/60 px-4 py-2.5 text-xs text-signal-800">
                <span>Turn this text dump into structured, editable sections (education, roles, projects, skill categories).</span>
                <button
                  type="button"
                  disabled={structure.isPending}
                  onClick={() => structure.mutate(profile.id)}
                  className="control-focus rounded-lg bg-signal-600 px-2.5 py-1 font-semibold text-white hover:bg-signal-700 disabled:opacity-50"
                >
                  {structure.isPending ? 'Structuring… (1 AI call)' : '✨ Structure my resume'}
                </button>
              </div>
            )}
            {profile.resume_sections.length === 0 && (
              <div className="workspace-surface px-4 py-6 text-center text-sm text-slate-400">
                No resume sections yet — upload a resume or add a custom section below.
              </div>
            )}
            {profile.resume_sections.map((section, i) => (
              <section key={`${section.heading}-${i}`} className="workspace-surface px-4 py-3.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-signal-50 text-signal-600">
                      {sectionIcon(section.heading)}
                    </span>
                    <h2 className="text-sm font-semibold tracking-[-0.02em] text-ink [overflow-wrap:anywhere]">{section.heading || 'Untitled section'}</h2>
                    <span className="tag bg-slate-100 font-mono text-[0.62rem] text-slate-500">{contentLines(section.content).length} {contentLines(section.content).length === 1 ? 'item' : 'items'}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setEdit({ kind: 'section', index: i })}
                      className="control-focus flex h-7 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
                    >
                      <PencilSimple size={12} /> Edit
                    </button>
                    <button
                      type="button"
                      aria-label="Delete section"
                      onClick={() => {
                        if (window.confirm(`Remove the "${section.heading}" section? Matching will stop using its text.`))
                          save((p) => { p.resume_sections = p.resume_sections.filter((_, j) => j !== i); return p })
                      }}
                      className="control-focus rounded-lg p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500"
                    >
                      <Trash size={13} />
                    </button>
                  </div>
                </div>
                <ul className="mt-2.5 space-y-1.5 text-[0.82rem] leading-relaxed text-slate-600">
                  {contentLines(section.content).slice(0, 8).map((line, j) => (
                    <li key={j} className="flex gap-2">
                      <span className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-signal-300" />
                      <span className="min-w-0">{line}</span>
                    </li>
                  ))}
                  {contentLines(section.content).length > 8 && (
                    <li className="pl-3 text-xs italic text-slate-400">+{contentLines(section.content).length - 8} more…</li>
                  )}
                </ul>
              </section>
            ))}
            <button
              type="button"
              onClick={() => setEdit({ kind: 'new-section' })}
              className="control-focus flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 bg-white/60 px-4 py-4 text-sm font-medium text-slate-500 hover:border-signal-300 hover:text-signal-700"
            >
              <Plus size={15} /> Add custom section
            </button>
              </>
            )}
          </div>

          {/* ── Right: matching preferences ── */}
          <div className="space-y-3">
            <PrefCard
              icon={<UserCircle size={16} weight="duotone" />}
              title="Target roles"
              subtitle={`${profile.target_titles.length} ${profile.target_titles.length === 1 ? 'role drives' : 'roles drive'} title matching & the refill`}
              onEdit={() => openTags('target_titles', 'Edit target roles', 'e.g. data scientist, ml engineer')}
            >
              <Chips values={profile.target_titles} empty="No target roles — For You needs at least one." />
            </PrefCard>

            {profile.structured_resume ? (
              <SkillCategoriesCard
                profile={profile}
                onSave={(categories, flat) => save((p) => ({
                  ...p,
                  skills: flat,
                  structured_resume: p.structured_resume
                    ? { ...p.structured_resume, skill_categories: categories }
                    : p.structured_resume,
                }) as Profile)}
              />
            ) : (
              <PrefCard
                icon={<Lightning size={16} weight="duotone" />}
                title="Skills"
                subtitle={`${profile.skills.length} verified ${profile.skills.length === 1 ? 'skill' : 'skills'} (matched against every JD)`}
                onEdit={() => openTags('skills', 'Edit skills', 'e.g. python, sql, pytorch')}
              >
                <Chips values={profile.skills} accent max={24} empty="No skills yet — upload a resume or add them here." />
              </PrefCard>
            )}

            <PrefCard
              icon={<Sparkle size={16} weight="duotone" />}
              title="Interests"
              subtitle="Soft signal — nudges adjacent roles up, never gates"
              onEdit={() => openTags('interests', 'Edit interests', 'e.g. generative ai, healthcare tech')}
            >
              <Chips values={profile.interests} empty="Optional — add domains you'd love to work in." />
            </PrefCard>

            <PrefCard
              icon={<Sliders size={16} weight="duotone" />}
              title="Eligibility & work authorization"
              subtitle="Hard gates: these reject roles outright"
              onEdit={() => {
                setDraftBools({
                  needs_sponsorship: profile.needs_sponsorship,
                  reject_clearance: profile.reject_clearance,
                  reject_citizenship_only: profile.reject_citizenship_only,
                })
                setEdit({ kind: 'eligibility' })
              }}
            >
              <div className="flex flex-wrap gap-1.5">
                <BoolTag on={profile.needs_sponsorship} label="Needs visa sponsorship" />
                <BoolTag on={profile.reject_clearance} label="Skip clearance-required" />
                <BoolTag on={profile.reject_citizenship_only} label="Skip citizens-only" />
              </div>
            </PrefCard>

            <PrefCard
              icon={<Briefcase size={16} weight="duotone" />}
              title="Work preferences"
              subtitle="Seniority ceiling, experience cap, work mode"
              onEdit={() => {
                setDraftWork({
                  remote: profile.remote_preference,
                  seniority: profile.seniority_max,
                  yoe: profile.yoe_max,
                  capExempt: profile.prefer_cap_exempt,
                })
                setEdit({ kind: 'work-prefs' })
              }}
            >
              <div className="flex flex-wrap gap-1.5">
                <span className="tag bg-slate-100 text-slate-600">Up to {profile.seniority_max}</span>
                <span className="tag bg-slate-100 text-slate-600">≤ {profile.yoe_max} yrs required</span>
                <span className="tag bg-slate-100 text-slate-600">{profile.remote_preference === 'any' ? 'Any work mode' : profile.remote_preference}</span>
                {profile.prefer_cap_exempt && <span className="tag bg-signal-50 text-signal-700">Prefers cap-exempt</span>}
              </div>
            </PrefCard>

            <PrefCard
              icon={<Sparkle size={16} weight="duotone" />}
              title="Deep-match steering"
              subtitle="Role types & domains the AI second-opinion should skip"
              onEdit={() => openTags('avoid_role_types', 'Role types to avoid', 'e.g. pure BI/reporting work')}
              extraAction={{ label: 'Domains', onClick: () => openTags('avoid_domains', 'Domains to avoid', 'e.g. healthcare billing, Shopify') }}
            >
              <Chips
                values={[...(profile.avoid_role_types ?? []), ...(profile.avoid_domains ?? [])]}
                empty="Optional — steer the deep-match LLM away from mismatched work."
              />
            </PrefCard>

            <PrefCard
              icon={<Trash size={16} weight="duotone" />}
              title="Excluded companies"
              subtitle="Never recommend these employers"
              onEdit={() => openTags('excluded_companies', 'Excluded companies', 'e.g. Acme Corp')}
            >
              <Chips values={profile.excluded_companies ?? []} empty="None excluded." />
            </PrefCard>
          </div>
        </div>
      </div>

      {/* ── Modals ── */}
      {edit?.kind === 'section' && profile.resume_sections[edit.index] && (
        <SectionEditModal
          title={`Edit ${profile.resume_sections[edit.index].heading || 'section'}`}
          initial={profile.resume_sections[edit.index]}
          onClose={() => setEdit(null)}
          onSave={(next) => save((p) => { p.resume_sections[edit.index] = next; return p })}
        />
      )}
      {edit?.kind === 'new-section' && (
        <SectionEditModal
          title="New custom section"
          initial={{ heading: '', content: '' }}
          onClose={() => setEdit(null)}
          onSave={(next) => save((p) => { p.resume_sections = [...p.resume_sections, next]; return p })}
        />
      )}
      {edit?.kind === 'tags' && (
        <PrefsModalShell
          title={edit.title}
          onClose={() => setEdit(null)}
          onSave={() => save((p) => { (p as unknown as Record<string, unknown>)[edit.field] = draftTags; return p })}
        >
          <TagInput values={draftTags} onChange={setDraftTags} placeholder={edit.hint} accent={edit.field === 'skills'} />
          {SUGGESTIBLE_FIELDS.includes(edit.field) && (
            <SuggestChips
              profileId={profile.id}
              field={edit.field}
              current={draftTags}
              onAdd={(items) => setDraftTags((prev) => [...prev, ...items.filter((i) => !prev.includes(i))])}
            />
          )}
        </PrefsModalShell>
      )}
      {edit?.kind === 'raw-text' && (
        <PrefsModalShell
          title="Raw resume text"
          onClose={() => setEdit(null)}
          onSave={() => save((p) => ({ ...p, resume_text: draftText }) as Profile)}
        >
          <p className="mb-2 text-xs text-slate-400">
            The canonical flat text behind semantic matching. Saving re-derives the section cards and
            re-embeds matching automatically. <span className="font-mono">{draftText.length.toLocaleString()}</span> chars.
          </p>
          <textarea
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
            rows={16}
            spellCheck={false}
            className="control-focus w-full rounded-lg border border-slate-300 p-3 font-mono text-xs leading-relaxed text-ink"
          />
        </PrefsModalShell>
      )}
      {edit?.kind === 'eligibility' && (
        <PrefsModalShell
          title="Eligibility & work authorization"
          onClose={() => setEdit(null)}
          onSave={() => save((p) => ({ ...p, ...draftBools }) as Profile)}
        >
          <div className="space-y-1">
            <ToggleRow label="Needs visa sponsorship" description="Rejects explicit no-sponsorship roles; flags unstated ones" checked={!!draftBools.needs_sponsorship} onChange={(v) => setDraftBools((d) => ({ ...d, needs_sponsorship: v }))} />
            <ToggleRow label="Skip clearance-required roles" description="Security-clearance requirements are a hard wall" checked={!!draftBools.reject_clearance} onChange={(v) => setDraftBools((d) => ({ ...d, reject_clearance: v }))} />
            <ToggleRow label="Skip citizens-only roles" description="US-citizen / green-card / ITAR requirements" checked={!!draftBools.reject_citizenship_only} onChange={(v) => setDraftBools((d) => ({ ...d, reject_citizenship_only: v }))} />
          </div>
        </PrefsModalShell>
      )}
      {edit?.kind === 'work-prefs' && (
        <PrefsModalShell
          title="Work preferences"
          onClose={() => setEdit(null)}
          onSave={() => save((p) => ({
            ...p,
            remote_preference: draftWork.remote,
            seniority_max: draftWork.seniority,
            yoe_max: draftWork.yoe,
            prefer_cap_exempt: draftWork.capExempt,
          }) as Profile)}
        >
          <div className="space-y-4">
            <div>
              <label className="section-label mb-1 block">Seniority ceiling</label>
              <select value={draftWork.seniority} onChange={(e) => setDraftWork((d) => ({ ...d, seniority: e.target.value }))} className="control-focus w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
                {SENIORITY_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
            <div>
              <label className="section-label mb-1 block">Max years of experience required</label>
              <input type="number" min={0} max={15} value={draftWork.yoe} onChange={(e) => setDraftWork((d) => ({ ...d, yoe: Number(e.target.value) }))} className="control-focus w-28 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="section-label mb-1 block">Work mode</label>
              <select value={draftWork.remote} onChange={(e) => setDraftWork((d) => ({ ...d, remote: e.target.value as Profile['remote_preference'] }))} className="control-focus w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
                {(['any', 'remote', 'hybrid', 'onsite'] as const).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
            <ToggleRow label="Prefer cap-exempt employers" description="Universities / hospitals / nonprofits rank first (off-lottery H-1B)" checked={draftWork.capExempt} onChange={(v) => setDraftWork((d) => ({ ...d, capExempt: v }))} />
          </div>
        </PrefsModalShell>
      )}
    </div>
  )
}

// ── small presentational helpers ─────────────────────────────────────────────

function PrefCard({
  icon, title, subtitle, onEdit, children, extraAction,
}: {
  icon: ReactNode
  title: string
  subtitle: string
  onEdit: () => void
  children: ReactNode
  extraAction?: { label: string; onClick: () => void }
}) {
  return (
    <section className="workspace-surface px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500">{icon}</span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-[-0.02em] text-ink">{title}</h2>
            <p className="truncate text-[0.68rem] text-slate-400">{subtitle}</p>
          </div>
        </div>
        <div className="flex shrink-0 gap-1">
          {extraAction && (
            <button type="button" onClick={extraAction.onClick} className="control-focus flex h-7 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 text-xs font-medium text-slate-600 hover:bg-slate-50">
              <PencilSimple size={12} /> {extraAction.label}
            </button>
          )}
          <button type="button" onClick={onEdit} className="control-focus flex h-7 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 text-xs font-medium text-slate-600 hover:bg-slate-50">
            <PencilSimple size={12} /> Edit
          </button>
        </div>
      </div>
      <div className="mt-2.5">{children}</div>
    </section>
  )
}

function Chips({ values, empty, accent = false, max = 16 }: { values: string[]; empty: string; accent?: boolean; max?: number }) {
  if (!values.length) return <p className="text-xs italic text-slate-400">{empty}</p>
  const shown = values.slice(0, max)
  return (
    <div className="flex flex-wrap gap-1.5">
      {shown.map((v) => (
        <span key={v} className={`tag ${accent ? 'bg-signal-50 text-signal-800' : 'bg-slate-100 text-slate-600'}`}>{v}</span>
      ))}
      {values.length > max && <span className="tag bg-slate-50 text-slate-400">+{values.length - max} more</span>}
    </div>
  )
}

function BoolTag({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={`tag ${on ? 'bg-signal-50 text-signal-700' : 'bg-slate-100 text-slate-400 line-through decoration-slate-300'}`}>
      {on ? '✓ ' : ''}{label}
    </span>
  )
}
