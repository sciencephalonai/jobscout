import { useState, type ReactNode } from 'react'
import { CircleNotch, Warning, X } from '@phosphor-icons/react'
import TopNav from './TopNav'
import { useHealth } from '../api/client'

/**
 * The shared product shell. Keeping navigation outside individual routes gives
 * the discovery, tracker, profile and company views one coherent workspace
 * rather than five unrelated pages.
 */
export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="h-dvh min-h-0 overflow-hidden bg-[#eef0ed] text-[#151a1f] xl:flex">
      <TopNav />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden pt-14 xl:pt-0">
        <SeedingBanner />
        <HealthBanner />
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </main>
    </div>
  )
}

/** Shown once on a fresh deployment while the first-run seed fetches initial jobs. */
function SeedingBanner() {
  const { data } = useHealth()
  if (!data?.seeding) return null
  return (
    <div className="flex items-center gap-2.5 border-b border-signal-200 bg-signal-50 px-4 py-2.5 text-xs text-signal-800" role="status">
      <CircleNotch size={15} className="shrink-0 animate-spin text-signal-500" />
      <span><span className="font-semibold">Fetching your first jobs…</span> This one-time setup runs in the background — the feed will fill in shortly.</span>
    </div>
  )
}

/** Actionable setup banner: shows exactly which key/service is missing and how to fix it. */
function HealthBanner() {
  const { data } = useHealth()
  const [dismissed, setDismissed] = useState(false)
  const problems = data?.problems ?? []
  if (dismissed || problems.length === 0) return null
  return (
    <div className="flex items-start gap-2.5 border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900" role="alert">
      <Warning size={16} weight="fill" className="mt-0.5 shrink-0 text-amber-500" />
      <div className="min-w-0 flex-1 space-y-1">
        {problems.map((p) => (
          <p key={p.key}>
            <span className="font-semibold">{p.message}</span>{' '}
            <span className="text-amber-700">{p.fix}</span>
          </p>
        ))}
      </div>
      <button type="button" aria-label="Dismiss" onClick={() => setDismissed(true)} className="shrink-0 rounded p-0.5 text-amber-400 hover:bg-amber-100 hover:text-amber-700">
        <X size={14} />
      </button>
    </div>
  )
}
