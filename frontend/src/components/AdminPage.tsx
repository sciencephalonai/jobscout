// Operator/admin console (host-only). Monitor accounts — plan, storage, per-user LLM
// usage, traffic — and grant/revoke premium. Backed by /api/admin/*, which is
// require_admin-gated; this page is only linked when the current user is_admin.
// Usage numbers populate once `usage_metering_enabled` is turned on (see the checklist).
import { useAdminMetrics, useAdminUsers, useUpdateUser, type AdminUser } from '../api/client'

const PLANS = ['local', 'free', 'pro', 'unlimited']

function humanBytes(n: number): string {
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function sumUsage(u: Record<string, number>, keys: string[]): number {
  return keys.reduce((t, k) => t + (u[k] || 0), 0)
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="workspace-surface px-4 py-3">
      <p className="section-label">{label}</p>
      <p className="mt-1 text-xl font-semibold tracking-[-0.02em] text-ink">{value}</p>
    </div>
  )
}

function PlanBadge({ plan }: { plan: string | null }) {
  const cls = plan === 'unlimited' ? 'bg-signal-100 text-signal-800'
    : plan === 'pro' ? 'bg-emerald-100 text-emerald-800'
    : plan === 'free' ? 'bg-slate-100 text-slate-600'
    : 'bg-slate-100 text-slate-500'
  return <span className={`tag ${cls}`}>{plan || 'local'}</span>
}

function UserRow({ user }: { user: AdminUser }) {
  const update = useUpdateUser()
  return (
    <tr className="border-t border-slate-100">
      <td className="py-2 pr-3">
        <p className="font-medium text-ink [overflow-wrap:anywhere]">{user.display_name || user.email || user.id}</p>
        <p className="font-mono text-[0.62rem] text-slate-400">{user.id}</p>
      </td>
      <td className="py-2 pr-3">
        <div className="flex items-center gap-1.5">
          <PlanBadge plan={user.plan} />
          <select
            aria-label="Change plan"
            value={user.plan || 'local'}
            onChange={(e) => update.mutate({ userId: user.id, plan: e.target.value })}
            className="control-focus rounded-lg border border-slate-200 bg-white px-1.5 py-1 text-xs text-slate-700"
          >
            {PLANS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
      </td>
      <td className="py-2 pr-3 text-center">
        <input
          type="checkbox" aria-label="Admin"
          checked={user.is_admin}
          onChange={(e) => update.mutate({ userId: user.id, is_admin: e.target.checked })}
        />
      </td>
      <td className="py-2 pr-3 text-right tabular-nums">{user.profile_count}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{humanBytes(user.storage_bytes)}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{sumUsage(user.usage_30d, ['tailor'])}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{sumUsage(user.usage_30d, ['deep_match'])}</td>
      <td className="py-2 text-right tabular-nums">{sumUsage(user.usage_30d, ['requests'])}</td>
    </tr>
  )
}

export default function AdminPage() {
  const { data: metrics } = useAdminMetrics()
  const { data, isLoading, isError, error } = useAdminUsers()
  const users = data?.users ?? []

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-4 py-5">
      <div>
        <h1 className="text-lg font-semibold tracking-[-0.02em] text-ink">Admin · operator console</h1>
        <p className="mt-0.5 text-xs text-slate-500">
          Monitor accounts and grant/revoke premium. Usage fills in once
          <b className="text-slate-600"> usage metering</b> is enabled.
        </p>
      </div>

      {metrics && (
        <div className="grid gap-2.5 sm:grid-cols-4">
          <MetricCard label="Users" value={String(metrics.user_count)} />
          <MetricCard label="Storage" value={humanBytes(metrics.storage_bytes)} />
          <MetricCard label="LLM (30d)" value={String(sumUsage(metrics.usage_30d, ['tailor', 'deep_match', 'llm_call']))} />
          <MetricCard label="Requests (30d)" value={String(sumUsage(metrics.usage_30d, ['requests']))} />
        </div>
      )}
      {metrics && !metrics.metering_enabled && (
        <p className="rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Usage metering is <b>off</b> — usage numbers stay at 0. Set <code>usage_metering_enabled=true</code> to
          start collecting (no caps unless <code>quota_enforced</code> is also on). See the pre-deployment checklist.
        </p>
      )}

      <section className="workspace-surface overflow-x-auto px-4 py-3.5">
        <h2 className="mb-2 text-sm font-semibold tracking-[-0.02em] text-ink">Accounts</h2>
        {isLoading && <p className="text-xs text-slate-500">Loading…</p>}
        {isError && <p className="text-xs text-rose-600">{error?.message}</p>}
        {!isLoading && !isError && (
          <table className="w-full min-w-[640px] text-xs">
            <thead>
              <tr className="text-left text-[0.62rem] uppercase tracking-wide text-slate-400">
                <th className="pb-1 pr-3 font-semibold">User</th>
                <th className="pb-1 pr-3 font-semibold">Plan</th>
                <th className="pb-1 pr-3 text-center font-semibold">Admin</th>
                <th className="pb-1 pr-3 text-right font-semibold">Profiles</th>
                <th className="pb-1 pr-3 text-right font-semibold">Storage</th>
                <th className="pb-1 pr-3 text-right font-semibold">Tailors</th>
                <th className="pb-1 pr-3 text-right font-semibold">Deep</th>
                <th className="pb-1 text-right font-semibold">Requests</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => <UserRow key={u.id} user={u} />)}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
