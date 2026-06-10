import { useActiveProfile } from '../ProfileContext'
import { useProfiles, useDeleteProfileById } from '../api/client'

/**
 * Headerless profiles manager (list / set-active / delete). Embedded in the
 * Profile page under the shared TopNav.
 */
export function ProfilesList() {
  const { data: profiles, isLoading } = useProfiles()
  const del = useDeleteProfileById()
  const { activeProfileId, setActiveProfileId } = useActiveProfile()

  return (
    <div>
        <h2 className="text-base font-semibold text-slate-900">Saved profiles</h2>
        <p className="mb-4 text-sm text-slate-500">
          Profiles are created by dropping a resume above. They are stored locally
          (DuckDB) and drive verdicts, sorting, and your My Jobs lists. Delete any
          time — no account, fully local.
        </p>

        {isLoading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : !profiles || profiles.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-400">
            No profiles yet. Go to the Match tab and drop your resume.
          </div>
        ) : (
          <div className="space-y-3">
            {profiles.map((p) => (
              <div key={p.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="font-medium text-slate-800">{p.label}</h2>
                      {p.id === activeProfileId && (
                        <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">active</span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {p.yoe_max} yrs · {p.seniority_max}{p.needs_sponsorship ? ' · needs sponsorship' : ''}
                    </p>
                    <p className="mt-1 text-xs text-slate-500"><span className="font-medium">Targets:</span> {p.target_titles.join(', ')}</p>
                    <p className="mt-1 text-xs text-slate-400">{p.skills.slice(0, 18).join(', ')}</p>
                  </div>
                  <div className="flex flex-shrink-0 gap-2">
                    {p.id !== activeProfileId && (
                      <button type="button" onClick={() => setActiveProfileId(p.id)}
                        className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
                        Set active
                      </button>
                    )}
                    <button type="button"
                      onClick={() => {
                        del.mutate(p.id)
                        if (p.id === activeProfileId) setActiveProfileId(null)
                      }}
                      className="rounded-lg border border-rose-300 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50">
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
    </div>
  )
}
