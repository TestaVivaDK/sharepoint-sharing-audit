import { useIsAuthenticated, useMsal } from '@azure/msal-react'
import { useEffect, useState } from 'react'
import { LoginPage } from './auth/LoginPage'
import { loginScopes } from './auth/msalConfig'

function App() {
  const isAuthenticated = useIsAuthenticated()
  const { instance, accounts } = useMsal()
  const [sessionReady, setSessionReady] = useState(false)

  useEffect(() => {
    if (!isAuthenticated || accounts.length === 0) return

    // Get ID token and send to backend to create session
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
  if (!sessionReady) return <div>Establishing session...</div>

  return <div>Dashboard placeholder — Task 11 will build this</div>
}

export default App
