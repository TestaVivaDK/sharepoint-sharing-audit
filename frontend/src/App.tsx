import { useIsAuthenticated, useMsal } from '@azure/msal-react'
import { useEffect, useState } from 'react'
import { LoginPage } from './auth/LoginPage'
import { Dashboard } from './components/Dashboard'
import { loginScopes } from './auth/msalConfig'
import { Box, CircularProgress, Typography } from '@mui/material'

function App() {
  const isAuthenticated = useIsAuthenticated()
  const { instance, accounts } = useMsal()
  const [sessionReady, setSessionReady] = useState(false)

  useEffect(() => {
    if (!isAuthenticated || accounts.length === 0) return

    instance.acquireTokenSilent({
      scopes: loginScopes,
      account: accounts[0],
    }).then(async (response) => {
      await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: response.idToken }),
      })
      setSessionReady(true)
    }).catch(console.error)
  }, [isAuthenticated, accounts, instance])

  if (!isAuthenticated) return <LoginPage />

  if (!sessionReady) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mt: 10, gap: 2 }}>
        <CircularProgress />
        <Typography>Establishing session...</Typography>
      </Box>
    )
  }

  return <Dashboard />
}

export default App
