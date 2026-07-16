import { useState } from 'react'
import { UploadSimple } from '@phosphor-icons/react'
import { StateList } from './StatePanel'
import PipelineView from './PipelineView'
import { useImportApplied } from '../api/client'
import { useActiveProfile } from '../ProfileContext'
import Modal from './ui/Modal'

type Segment = 'saved' | 'pipeline'

/**
 * "My Jobs" — one page: Shortlist (saved jobs) + Pipeline (application tracker
 * applied→oa→interview→offer→rejected). Replaces the old /shortlist + /applied tabs.
 */
export default function MyJobsPanel() {
  const [tab, setTab] = useState<Segment>('saved')
  const [showImport, setShowImport] = useState(false)

  const segments: { key: Segment; label: string; blurb: string }[] = [
    { key: 'saved', label: 'Shortlist', blurb: 'Jobs you saved to act on. Sorted by fit when a profile is active.' },
    { key: 'pipeline', label: 'Pipeline', blurb: 'Application tracker: applied → OA → interview → offer. Move a job through stages; it leaves the main Jobs list.' },
  ]
  const active = segments.find((s) => s.key === tab)!

  return (
    <div className="page-shell">
      <div className="mx-auto w-full max-w-6xl">
        <header className="page-header">
          <div>
            <h1 className="page-title">My jobs</h1>
            <p className="page-description">{active.blurb}</p>
          </div>
        <div className="flex items-center gap-2">
        <button type="button" onClick={() => setShowImport(true)}
          title="Paste a markdown tracker table to mark those roles applied (no AI, no network)"
          className="control-focus flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
          <UploadSimple size={14} /> Import applied
        </button>
        <div className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5" role="tablist" aria-label="Application workspace view">
          {segments.map((s) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              aria-selected={tab === s.key}
              onClick={() => setTab(s.key)}
              className={`control-focus h-8 rounded-md px-3 text-xs font-medium ${
                tab === s.key ? 'bg-ink text-white' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        </div>
        </header>
        <div>{tab === 'saved' ? <StateList status="saved" /> : <PipelineView />}</div>
      </div>
      {showImport && <ImportAppliedModal onClose={() => setShowImport(false)} />}
    </div>
  )
}

/** Paste a markdown tracker table (Date | Company | Role | Link | Notes) → mark applied. */
function ImportAppliedModal({ onClose }: { onClose: () => void }) {
  const { activeProfileId } = useActiveProfile()
  const [text, setText] = useState('')
  const importer = useImportApplied()
  const result = importer.data

  return (
    <Modal
      title="Import applied jobs"
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" onClick={onClose} className="control-focus rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">Close</button>
          <button
            type="button"
            disabled={!activeProfileId || !text.trim() || importer.isPending}
            onClick={() => activeProfileId && importer.mutate({ profileId: activeProfileId, text })}
            className="control-focus rounded-lg bg-ink px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-ink-soft disabled:opacity-40"
          >
            {importer.isPending ? 'Matching…' : 'Mark as applied'}
          </button>
        </>
      }
    >
      <p className="mb-2 text-xs text-slate-500">
        Paste your tracker table. Rows are matched by link first, then company + role. Matches are
        marked <b>applied</b> and drop out of the feed. No AI call, nothing leaves this machine.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={10}
        spellCheck={false}
        placeholder={'| Date | Company | Role | Link | Notes |\n|---|---|---|---|---|\n| 2026-07-01 | Verve | Data Engineer | https://boards.greenhouse.io/... | |'}
        className="control-focus w-full rounded-lg border border-slate-300 p-3 font-mono text-xs leading-relaxed text-ink"
      />
      {importer.error && <p className="mt-2 text-xs text-rose-600">{importer.error.message}</p>}
      {result && (
        <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
          <p className="font-semibold">Marked {result.marked_applied} of {result.rows} rows as applied.</p>
          {result.unmatched.length > 0 && (
            <>
              <p className="mt-1 text-emerald-800">Not found in the index (ingest them first, or they were never indexed):</p>
              <ul className="mt-0.5 list-disc pl-4 text-emerald-700">
                {result.unmatched.map((u) => <li key={u}>{u}</li>)}
              </ul>
            </>
          )}
        </div>
      )}
    </Modal>
  )
}
