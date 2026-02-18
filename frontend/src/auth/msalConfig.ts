import type { Configuration } from '@azure/msal-browser'

export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_CLIENT_ID || '',
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_TENANT_ID || 'common'}`,
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: 'sessionStorage',
  },
}

export const loginScopes = ['User.Read']
export const graphScopes = ['Files.ReadWrite.All']
