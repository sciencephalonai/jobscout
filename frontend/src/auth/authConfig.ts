// Auth0 SPA config from Vite env. Auth is active only when a domain + client id
// are present; otherwise the app runs unauthenticated (local dev), exactly as before.
export const authDomain = import.meta.env.VITE_AUTH0_DOMAIN as string | undefined
export const authClientId = import.meta.env.VITE_AUTH0_CLIENT_ID as string | undefined
export const authAudience = import.meta.env.VITE_AUTH0_AUDIENCE as string | undefined

export const authConfigured = Boolean(authDomain && authClientId)
