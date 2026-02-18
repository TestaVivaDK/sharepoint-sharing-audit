import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useMsal } from '@azure/msal-react'
import { graphScopes } from '../auth/msalConfig'
import type { FilesResponse, StatsResponse, UnshareResponse } from './types'

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, { credentials: 'include', ...options })
  if (!resp.ok) throw new Error(`API error: ${resp.status}`)
  return resp.json()
}

export function useFiles(filters?: { risk_level?: string; source?: string; search?: string }) {
  const params = new URLSearchParams()
  if (filters?.risk_level) params.set('risk_level', filters.risk_level)
  if (filters?.source) params.set('source', filters.source)
  if (filters?.search) params.set('search', filters.search)
  const qs = params.toString()

  return useQuery({
    queryKey: ['files', filters],
    queryFn: () => apiFetch<FilesResponse>(`/api/files${qs ? `?${qs}` : ''}`),
  })
}

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch<StatsResponse>('/api/stats'),
  })
}

export function useUnshare() {
  const queryClient = useQueryClient()
  const { instance, accounts } = useMsal()

  return useMutation({
    mutationFn: async (fileIds: string[]) => {
      // Acquire Graph API token — fall back to popup for consent
      let tokenResponse
      try {
        tokenResponse = await instance.acquireTokenSilent({
          scopes: graphScopes,
          account: accounts[0],
        })
      } catch {
        tokenResponse = await instance.acquireTokenPopup({
          scopes: graphScopes,
          account: accounts[0],
        })
      }

      return apiFetch<UnshareResponse>('/api/unshare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_ids: fileIds,
          graph_token: tokenResponse.accessToken,
        }),
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['files'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}
