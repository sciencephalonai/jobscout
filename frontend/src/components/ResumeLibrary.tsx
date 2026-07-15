// Resume library + tailored-resume library for the Profile tab.
// - Resumes: many uploads per profile, exactly one Active (drives matching).
//   Per row: set active · download · rename · delete, plus an "upload another"
//   dropzone. Switching active re-scores For You (the backend clears the cache).
// - Tailored: every DOCX built for a job, downloadable anytime with a dated name.
// Copy rule: this is product chrome → sentence case (see ProfilePanel).
import { useRef, useState } from 'react' // useRef: one-commit-per-edit guard
import {
  ArrowUpRight, Check, DownloadSimple, FileText, PencilSimple, Plus, Sparkle, Trash,
} from '@phosphor-icons/react'
import {
  useActivateResume, useDeleteResume, useRenameResume, useRenameTailored, useResumes,
  useTailoredResumes, useUploadResume, type ResumeRow, type TailoredRow,
} from '../api/client'

function humanSize(bytes: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function shortDate(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function ResumeRowItem({
  profileId, row, active, onActivate, busy,
}: {
  profileId: string; row: ResumeRow; active: boolean
  onActivate: () => void; busy: boolean
}) {
  const rename = useRenameResume()
  const remove = useDeleteResume()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(row.filename)
  // Enter commits, then the input unmounts and blur would commit AGAIN — one per session.
  const committedRef = useRef(false)

  const commit = () => {
    if (committedRef.current) return
    committedRef.current = true
    const next = name.trim()
    setEditing(false)
    if (next && next !== row.filename) rename.mutate({ profileId, resumeId: row.id, filename: next })
    else setName(row.filename)
  }

  return (
    <div className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 ${active ? 'border-signal-200 bg-signal-50/50' : 'border-slate-200 bg-white'}`}>
      <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${active ? 'bg-signal-100 text-signal-700' : 'bg-slate-100 text-slate-500'}`}>
        <FileText size={16} weight="duotone" />
      </span>
      <div className="min-w-0 flex-1">
        {editing ? (
          <input
            autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { committedRef.current = true; setName(row.filename); setEditing(false) } }}
            className="control-focus w-full rounded-lg border border-slate-300 px-2 py-1 text-sm text-ink"
          />
        ) : (
          <div className="flex items-center gap-1.5">
            <p className="min-w-0 truncate text-sm font-medium text-ink [overflow-wrap:anywhere]" title={row.filename}>{row.filename}</p>
            <button type="button" aria-label="Rename resume" onClick={() => { committedRef.current = false; setName(row.filename); setEditing(true) }}
              className="control-focus shrink-0 rounded p-0.5 text-slate-300 hover:bg-slate-100 hover:text-slate-600"><PencilSimple size={12} /></button>
          </div>
        )}
        {rename.error && <p className="mt-0.5 text-xs text-rose-600">Rename failed: {rename.error.message}</p>}
        {rename.data && rename.variables && rename.data.filename !== rename.variables.filename && (
          <p className="mt-0.5 text-xs text-amber-600">Name taken — saved as “{rename.data.filename}”</p>
        )}
        <p className="mt-0.5 text-[0.68rem] text-slate-400">
          {[shortDate(row.uploaded_at), humanSize(row.size_bytes)].filter(Boolean).join(' · ')}
          {active && <span className="ml-1.5 font-semibold text-signal-600">· Active for matching</span>}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {active ? (
          <span className="tag bg-signal-50 font-semibold text-signal-700"><Check size={11} weight="bold" /> Active</span>
        ) : (
          <button type="button" onClick={onActivate} disabled={busy}
            className="control-focus rounded-lg border border-slate-300 bg-white px-2 py-1 text-[0.7rem] font-semibold text-slate-600 hover:border-signal-300 hover:text-signal-700 disabled:opacity-50">
            Set active
          </button>
        )}
        <a href={`/api/profiles/${profileId}/resumes/${row.id}/file`} title="Download this resume"
          className="control-focus rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-ink"><DownloadSimple size={14} /></a>
        <button type="button" aria-label="Delete resume" title="Delete this resume"
          onClick={() => { if (window.confirm(`Delete "${row.filename}"? ${active ? 'The next resume becomes active for matching.' : ''}`)) remove.mutate({ profileId, resumeId: row.id }) }}
          className="control-focus rounded-lg p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500"><Trash size={14} /></button>
      </div>
    </div>
  )
}

function ResumesCard({ profileId }: { profileId: string }) {
  const { data } = useResumes(profileId)
  const upload = useUploadResume()
  const activate = useActivateResume()
  const fileRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const rows = data?.resumes ?? []
  const activeId = data?.active_resume_id ?? null
  const send = (file?: File | null) => { if (file) upload.mutate({ profileId, file }) }

  return (
    <section className="workspace-surface px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
          <h2 className="text-sm font-semibold tracking-[-0.02em] text-ink">Resumes</h2>
          <span className="tag bg-slate-100 font-mono text-[0.62rem] text-slate-500">
            {rows.length} {rows.length === 1 ? 'resume' : 'resumes'}
          </span>
        </div>
        <button type="button" onClick={() => fileRef.current?.click()} disabled={upload.isPending}
          className="control-focus flex h-7 shrink-0 items-center gap-1 rounded-lg bg-ink px-2 text-xs font-semibold text-white hover:bg-ink-soft disabled:opacity-50">
          <Plus size={12} /> {upload.isPending ? 'Uploading…' : 'Upload another'}
        </button>
        <input ref={fileRef} type="file" accept=".pdf,.docx,.doc,.txt,.json,.md" className="hidden"
          onChange={(e) => { send(e.target.files?.[0]); e.target.value = '' }} />
      </div>
      <p className="mt-1 text-[0.7rem] leading-4 text-slate-500">
        Keep several resumes and pick one as <b className="text-slate-600">active</b> — matching uses only the active resume.
      </p>
      {upload.error && <p className="mt-1.5 text-xs text-rose-600">{upload.error.message}</p>}

      <div className="mt-3 space-y-2"
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); send(e.dataTransfer.files?.[0]) }}>
        {rows.map((row) => (
          <ResumeRowItem key={row.id} profileId={profileId} row={row} active={row.id === activeId}
            busy={activate.isPending} onActivate={() => activate.mutate({ profileId, resumeId: row.id })} />
        ))}
        {rows.length === 0 && (
          <button type="button" onClick={() => fileRef.current?.click()}
            className={`flex w-full flex-col items-center justify-center gap-1 rounded-xl border border-dashed px-3 py-6 text-xs ${dragging ? 'border-signal-400 bg-signal-50 text-signal-700' : 'border-slate-300 text-slate-400 hover:border-slate-400 hover:text-slate-600'}`}>
            <FileText size={20} weight="duotone" />
            Drop a PDF/DOCX here or click to add your first resume
          </button>
        )}
      </div>
    </section>
  )
}

function TailoredCard({ profileId }: { profileId: string }) {
  const { data } = useTailoredResumes(profileId)
  const rows = data?.tailored ?? []
  if (rows.length === 0) return null

  return (
    <section className="workspace-surface px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-signal-50 text-signal-600"><Sparkle size={15} weight="duotone" /></span>
          <h2 className="text-sm font-semibold tracking-[-0.02em] text-ink">Tailored resumes</h2>
          <span className="tag bg-slate-100 font-mono text-[0.62rem] text-slate-500">
            {rows.length} {rows.length === 1 ? 'build' : 'builds'}
          </span>
        </div>
      </div>
      <div className="mt-3 space-y-2">
        {rows.map((row) => (
          <TailoredRowItem key={row.job_id} profileId={profileId} row={row} />
        ))}
      </div>
    </section>
  )
}

function TailoredRowItem({ profileId, row }: { profileId: string; row: TailoredRow }) {
  const rename = useRenameTailored()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(row.filename)
  const committedRef = useRef(false)  // Enter + blur both fire — commit once

  const commit = () => {
    if (committedRef.current) return
    committedRef.current = true
    const next = name.trim()
    setEditing(false)
    if (next && next !== row.filename) rename.mutate({ profileId, jobId: row.job_id, filename: next })
    else setName(row.filename)
  }

  return (
    <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-ink [overflow-wrap:anywhere]">{row.title || 'Tailored resume'}</p>
        <p className="mt-0.5 text-[0.68rem] text-slate-400">
          {[row.company, shortDate(row.created_at)].filter(Boolean).join(' · ')}
          {row.recommendation === 'skip' && <span className="ml-1.5 font-semibold text-amber-600">· built despite skip</span>}
          {row.up_to_date === false && <span className="ml-1.5 font-semibold text-amber-600">· resume changed — re-tailor</span>}
        </p>
        {editing ? (
          <input
            autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { committedRef.current = true; setName(row.filename); setEditing(false) } }}
            className="control-focus mt-1 w-full rounded-lg border border-slate-300 px-2 py-1 text-xs text-ink"
          />
        ) : row.filename ? (
          <div className="mt-0.5 flex items-center gap-1.5">
            <p className="min-w-0 truncate text-[0.62rem] text-slate-400" title={row.filename}>{row.filename}</p>
            <button type="button" aria-label="Rename download filename" onClick={() => { committedRef.current = false; setName(row.filename); setEditing(true) }}
              className="control-focus shrink-0 rounded p-0.5 text-slate-300 hover:bg-slate-100 hover:text-slate-600"><PencilSimple size={11} /></button>
          </div>
        ) : null}
        {rename.error && <p className="mt-0.5 text-xs text-rose-600">Rename failed: {rename.error.message}</p>}
        {rename.data && rename.variables && rename.data.filename !== rename.variables.filename && (
          <p className="mt-0.5 text-xs text-amber-600">Name taken — saved as “{rename.data.filename}”</p>
        )}
      </div>
      <a href={row.download_url} title="Download this tailored resume"
        className="control-focus flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
        <DownloadSimple size={14} /> DOCX <ArrowUpRight size={11} />
      </a>
    </div>
  )
}

export default function ResumeLibrary({ profileId }: { profileId: string }) {
  return (
    <div className="space-y-3">
      <ResumesCard profileId={profileId} />
      <TailoredCard profileId={profileId} />
    </div>
  )
}
