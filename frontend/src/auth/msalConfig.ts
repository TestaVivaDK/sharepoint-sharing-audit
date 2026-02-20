import type { Configuration } from '@azure/msal-browser'

declare global {
  interface Window { ENV?: { CLIENT_ID?: string; TENANT_ID?: string; MUI_LICENSE_KEY?: string } }
}

const clientId = window.ENV?.CLIENT_ID || import.meta.env.VITE_CLIENT_ID || ''
const tenantId = window.ENV?.TENANT_ID || import.meta.env.VITE_TENANT_ID || 'common'

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: 'sessionStorage',
  },
}

export const loginScopes = ['User.Read']
export const graphScopes = ['Files.ReadWrite.All']
