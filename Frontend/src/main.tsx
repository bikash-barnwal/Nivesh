import { ClerkProvider, Show, SignIn, useAuth } from '@clerk/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { setTokenGetter } from './lib/auth'
import { queryClient, router } from './router'
import './styles.css'

const clerkKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined

function TokenBridge({ children }: { children: ReactNode }) {
  const { getToken } = useAuth()
  setTokenGetter(() => getToken())
  return children
}

const app = (
  <QueryClientProvider client={queryClient}>
    <RouterProvider router={router} />
  </QueryClientProvider>
)

// No key (local dev) -> no login, same as before. With a key, the whole app sits behind Clerk sign-in.
createRoot(document.getElementById('root')!).render(
  clerkKey ? (
    <ClerkProvider publishableKey={clerkKey}>
      <Show when="signed-out">
        <div className="flex min-h-screen items-center justify-center">
          <SignIn routing="hash" />
        </div>
      </Show>
      <Show when="signed-in">
        <TokenBridge>{app}</TokenBridge>
      </Show>
    </ClerkProvider>
  ) : (
    app
  ),
)
