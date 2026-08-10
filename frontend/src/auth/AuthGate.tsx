// Gates the app behind Auth0 login when configured. In local mode (no Auth0 env)
// it renders children untouched. When configured, it shows a login screen until the
// user authenticates, and wires the access-token getter into apiFetch.
import { useAuth0 } from '@auth0/auth0-react'
import { useEffect, type ReactNode } from 'react'
import { setAuthTokenGetter } from '../api/client'
import { authConfigured } from './authConfig'

function FullScreen({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-center">
      <div className="max-w-sm space-y-4">{children}</div>
    </div>
  )
}

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  return (
    <FullScreen>
      <h1 className="text-2xl font-semibold text-slate-800">JobScout</h1>
      <p className="text-sm text-slate-500">Sign in to access your profiles, resumes, and pipeline.</p>
      <button
        type="button"
        onClick={onLogin}
        className="w-full rounded-lg bg-signal-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-signal-700"
      >
        Log in
      </button>
    </FullScreen>
  )
}

function AuthGateInner({ children }: { children: ReactNode }) {
  const { isLoading, isAuthenticated, error, loginWithRedirect, getAccessTokenSilently } = useAuth0()

  useEffect(() => {
    // apiFetch pulls a fresh access token per request while authenticated.
    setAuthTokenGetter(isAuthenticated ? () => getAccessTokenSilently() : null)
    return () => setAuthTokenGetter(null)
  }, [isAuthenticated, getAccessTokenSilently])

  if (isLoading) return <FullScreen><p className="text-sm text-slate-500">Loading…</p></FullScreen>
  if (error) {
    return (
      <FullScreen>
        <p className="text-sm text-rose-600">Sign-in failed: {error.message}</p>
        <button type="button" onClick={() => loginWithRedirect()}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100">
          Try again
        </button>
      </FullScreen>
    )
  }
  if (!isAuthenticated) return <LoginScreen onLogin={() => loginWithRedirect()} />
  return <>{children}</>
}

export function AuthGate({ children }: { children: ReactNode }) {
  if (!authConfigured) return <>{children}</>  // local mode — no provider, no gate
  return <AuthGateInner>{children}</AuthGateInner>
}
