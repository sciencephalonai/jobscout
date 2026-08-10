// Signed-in user chip + log out. Renders nothing in local mode (no Auth0), so it
// is safe to drop into the nav unconditionally.
import { useAuth0 } from '@auth0/auth0-react'
import { SignOut } from '@phosphor-icons/react'
import { authConfigured } from './authConfig'

function UserMenuInner() {
  const { user, logout } = useAuth0()
  if (!user) return null
  return (
    <button
      type="button"
      onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}
      title={`Signed in as ${user.email ?? user.name ?? 'account'} — log out`}
      className="control-focus flex h-8 w-full items-center gap-2 rounded-lg px-2 text-xs font-medium text-white/55 hover:bg-white/[0.055] hover:text-white"
    >
      <SignOut size={16} />
      <span className="min-w-0 flex-1 truncate text-left">{user.email ?? user.name ?? 'Account'}</span>
      <span className="shrink-0 text-white/40">Log out</span>
    </button>
  )
}

export function UserMenu() {
  if (!authConfigured) return null
  return <UserMenuInner />
}
