import { useRef, useState } from 'react'
import type { Job, Verdict } from '../types'
import { useMatchResume, useDeleteProfile } from '../api/client'
import { SponsorshipBadge } from './SponsorshipBadge'

function Chips({ items, cls, label }: { items: string[]; cls: string; label: string }) {
  if (!items || items.length === 0) return null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <span className="text-xs font-medium text-slate-400">{label}</span>
      {items.map((t) => (
        <span key={t} className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${cls}`}>
          {t}
        </span>
      ))}
    </div>
  )
}

function MatchRow({ job, verdict }: { job: Job; verdict?: Verdict }) {
  return (
    <div className="border-b border-slate-100 px-4 py-3 hover:bg-white">
      <div className="flex items-start justify-between gap-3">
        <div>
          <a href={job.url} target="_blank" rel="noopener noreferrer"
            className="font-medium text-slate-800 hover:text-blue-600">
            {job.title}
          </a>
          <p className="text-sm text-slate-500">
            {job.company}{job.location_raw ? ` · ${job.location_raw}` : ''}
          </p>
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          {verdict && (
            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">
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
  const inputRef = useRef<HTMLInputElement>(null)
  const match = useMatchResume()
  const del = useDeleteProfile()

  const onFile = (file: File | undefined) => {
    if (file) match.mutate({ file, limit: 12 })
  }

  const data = match.data
  const profile = data?.profile

  return (
    <div>
        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); onFile(e.dataTransfer.files?.[0]) }}
          onClick={() => inputRef.current?.click()}
          className={`cursor-pointer rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
            dragOver ? 'border-blue-500 bg-blue-50' : 'border-slate-300 bg-white hover:border-slate-400'
          }`}
        >
          <input
            ref={inputRef} type="file" className="hidden"
            accept=".pdf,.docx,.txt,.md,.json"
            onChange={(e) => onFile(e.target.files?.[0] ?? undefined)}
          />
          <p className="text-sm font-medium text-slate-700">
            {match.isPending ? 'Reading your resume…' : 'Drop your resume here, or click to choose a file'}
          </p>
          <p className="mt-1 text-xs text-slate-400">PDF, DOCX, TXT, or JSON · parsed locally, matched against live jobs</p>
        </div>

        {match.isError && (
          <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {match.error.message}
          </div>
        )}

        {/* Extracted profile */}
        {profile && (
          <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-start justify-between">
              <div>
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
                onClick={() => { del.mutate(profile.id); match.reset() }}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-500 hover:bg-slate-50"
              >
                Delete profile
              </button>
            </div>
            <Chips items={profile.skills} cls="bg-slate-100 text-slate-600" label="skills" />
          </div>
        )}

        {/* Matched jobs */}
        {data && (
          <div className="mt-6 overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
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
