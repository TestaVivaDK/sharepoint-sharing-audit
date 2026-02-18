import { MsalProvider } from '@azure/msal-react'
import { PublicClientApplication } from '@azure/msal-browser'
import { msalConfig } from './msalConfig'
import type { ReactNode } from 'react'

const msalInstance = new PublicClientApplication(msalConfig)

export function AuthProvider({ children }: { children: ReactNode }) {
  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>
}

export { msalInstance }
