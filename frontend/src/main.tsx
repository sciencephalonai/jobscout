import React from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource-variable/onest'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Auth0Provider } from '@auth0/auth0-react'
import App from './App'
import { AuthGate } from './auth/AuthGate'
import { authAudience, authClientId, authConfigured, authDomain } from './auth/authConfig'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

const app = (
  <QueryClientProvider client={queryClient}>
    <BrowserRouter>
      <AuthGate>
        <App />
      </AuthGate>
    </BrowserRouter>
  </QueryClientProvider>
)

// Wrap in Auth0Provider only when configured; otherwise the app runs unauthenticated
// (local dev), and AuthGate/UserMenu never call Auth0 hooks.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {authConfigured ? (
      <Auth0Provider
        domain={authDomain!}
        clientId={authClientId!}
        authorizationParams={{
          redirect_uri: window.location.origin,
          audience: authAudience,
        }}
        cacheLocation="localstorage"
      >
        {app}
      </Auth0Provider>
    ) : (
      app
    )}
  </React.StrictMode>,
)
