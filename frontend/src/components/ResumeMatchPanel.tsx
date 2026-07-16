import { useState } from 'react'
import { UploadSimple } from '@phosphor-icons/react'
import type { Job, Verdict } from '../types'
import { useMatchResume, useDeleteProfile } from '../api/client'
import { useActiveProfile } from '../ProfileContext'
import { SponsorshipBadge } from './SponsorshipBadge'

function Chips({ items, cls, label }: { items: string[]; cls: string; label: string }) {
  if (!items || items.length === 0) return null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <span className="text-xs font-medium text-slate-400">{label}</span>
      {items.slice(0, 8).map((t) => (
        <span key={t} className={`tag ${cls}`}>
          {t}
        </span>
      ))}
      {items.length > 8 && <span className="tag bg-slate-100 text-slate-500">+{items.length - 8}</span>}
    </div>
  )
}

function MatchRow({ job, verdict }: { job: Job; verdict?: Verdict }) {
  return (
    <div className="border-b border-slate-100 px-4 py-3 hover:bg-slate-50/70">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <a href={job.url} target="_blank" rel="noopener noreferrer"
            className="font-medium text-slate-800 hover:text-blue-600">
            {job.title}
          </a>
          <p className="text-sm text-slate-500">
            {job.company}{job.location_raw ? ` · ${job.location_raw}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:flex-shrink-0">
          {verdict && (
            <span className="tag bg-signal-50 font-semibold text-signal-800">
              fit {Math.round(verdict.score * 100)}%
            </span>
          )}
          <SponsorshipBadge job={job} />
        </div>
      </div>
      {verdict && (
        <>
          <Chips items={verdict.matched} cls="bg-emerald-100 text-emerald-700" label="matches" />
          <Chips items={verdict.gaps} cls="bg-amber-100 text-amber-700" label="gaps" />
        </>
      )}
    </div>
  )
}

/**
 * Headerless resume drop-zone + matches body. Embedded in the Profile page
 * (under the shared TopNav). No own header/nav.
 */
export function ResumeDropMatch() {
  const [dragOver, setDragOver] = useState(false)
  const match = useMatchResume()
  const del = useDeleteProfile()
  const { activeProfileId, setActiveProfileId } = useActiveProfile()

  const onFile = (file: File | undefined) => {
    if (file) {
      match.mutate(
        { file, limit: 12 },
        { onSuccess: ({ profile }) => setActiveProfileId(profile.id) },
      )
    }
  }

  const data = match.data
  const profile = data?.profile

  return (
    <div>
        {/* Drop zone */}
        <label
          htmlFor="resume-upload"
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); onFile(e.dataTransfer.files?.[0]) }}
          className={`control-focus flex cursor-pointer flex-col items-center rounded-xl border border-dashed px-6 py-8 text-center focus-within:ring-2 focus-within:ring-signal-500 ${
            dragOver ? 'border-signal-500 bg-signal-50' : 'border-slate-300 bg-white hover:border-signal-300 hover:bg-signal-50/30'
          }`}
        >
          <input
            id="resume-upload" type="file" className="sr-only"
            accept=".pdf,.docx,.txt,.md,.json"
            onChange={(e) => onFile(e.target.files?.[0] ?? undefined)}
          />
          <span className="mb-2 flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-slate-500"><UploadSimple size={18} /></span>
          <p className="text-sm font-medium text-slate-700">
            {match.isPending ? 'Reading your resume…' : 'Drop your resume here, or click to choose a file'}
          </p>
          <p className="mt-1 text-xs text-slate-500">PDF, DOCX, TXT, or JSON · parsed locally, matched against live jobs</p>
        </label>

        {match.isError && (
          <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {match.error.message}
          </div>
        )}

        {/* Extracted profile */}
        {profile && (
          <div className="workspace-surface mt-4 p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-slate-800">Extracted profile · {profile.label}</h2>
                <p className="mt-0.5 text-xs text-slate-500">
                  {profile.yoe_max} yrs · {profile.seniority_max}
                  {profile.needs_sponsorship ? ' · needs sponsorship' : ''}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  <span className="font-medium">Targets:</span> {profile.target_titles.join(', ')}
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (!window.confirm('Delete this profile and its saved matching evidence?')) return
                  del.mutate(profile.id, {
                    onSuccess: () => {
                      if (activeProfileId === profile.id) setActiveProfileId(null)
                      match.reset()
                    },
                  })
                }}
                className="control-focus h-8 rounded-lg px-3 text-xs font-medium text-rose-600 hover:bg-rose-50"
              >
                Delete profile
              </button>
            </div>
            <Chips items={profile.skills} cls="bg-slate-100 text-slate-600" label="skills" />
          </div>
        )}

        {/* Matched jobs */}
        {data && (
          <div className="workspace-surface mt-4 overflow-hidden">
            <div className="border-b border-slate-100 px-4 py-2 font-mono text-[0.64rem] font-semibold uppercase tracking-[0.08em] text-slate-500">
              {data.jobs.length} matched roles (ranked by fit + sponsorship)
            </div>
            {data.jobs.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-slate-400">
                No matches in the current index. Ingest more jobs (Jobs tab) and re-drop your resume.
              </p>
            ) : (
              data.jobs.map((j) => <MatchRow key={j.job_id} job={j} verdict={data.verdicts[j.job_id]} />)
            )}
          </div>
        )}
    </div>
  )
}
