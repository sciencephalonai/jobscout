import { useState } from 'react'
import TopNav from './TopNav'
import { StateList } from './StatePanel'
import PipelineView from './PipelineView'

type Segment = 'saved' | 'pipeline'

/**
 * "My Jobs" — one page: Shortlist (saved jobs) + Pipeline (application tracker
 * applied→oa→interview→offer→rejected). Replaces the old /shortlist + /applied tabs.
 */
export default function MyJobsPanel() {
  const [tab, setTab] = useState<Segment>('saved')

  const segments: { key: Segment; label: string; blurb: string }[] = [
    { key: 'saved', label: 'Shortlist', blurb: 'Jobs you saved to act on. Sorted by fit when a profile is active.' },
    { key: 'pipeline', label: 'Pipeline', blurb: 'Application tracker: applied → OA → interview → offer. Move a job through stages; it leaves the main Jobs list.' },
  ]
  const active = segments.find((s) => s.key === tab)!

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <TopNav />
      <div className="mx-auto w-full max-w-4xl flex-1 overflow-y-auto px-6 py-6">
        <h1 className="text-lg font-semibold text-slate-900">My Jobs</h1>
        {/* Segmented toggle */}
        <div className="mt-3 inline-flex rounded-lg border border-slate-200 bg-white p-0.5">
          {segments.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setTab(s.key)}
              className={`rounded-md px-4 py-1.5 text-sm font-medium transition ${
                tab === s.key ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <p className="mb-4 mt-2 text-sm text-slate-500">{active.blurb}</p>
        {tab === 'saved' ? <StateList status="saved" /> : <PipelineView />}
      </div>
    </div>
  )
}
