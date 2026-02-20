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
    onSuccess: (data) => {
      const removed = new Set(data.succeeded)
      if (removed.size === 0) return

      // Collect risk levels of removed files before evicting them
      const riskCounts = { HIGH: 0, MEDIUM: 0, LOW: 0 }
      const allCached = queryClient.getQueriesData<FilesResponse>({ queryKey: ['files'] })
      const seen = new Set<string>()
      for (const [, cached] of allCached) {
        if (!cached) continue
        for (const f of cached.files) {
          if (removed.has(f.id) && !seen.has(f.id)) {
            seen.add(f.id)
            riskCounts[f.risk_level]++
          }
        }
      }

      // Remove unshared files from all cached file lists
      queryClient.setQueriesData<FilesResponse>({ queryKey: ['files'] }, (old) => {
        if (!old) return old
        return { ...old, files: old.files.filter((f) => !removed.has(f.id)) }
      })

      // Decrement stats counts
      queryClient.setQueriesData<StatsResponse>({ queryKey: ['stats'] }, (old) => {
        if (!old) return old
        return {
          ...old,
          total: Math.max(0, old.total - removed.size),
          high: Math.max(0, old.high - riskCounts.HIGH),
          medium: Math.max(0, old.medium - riskCounts.MEDIUM),
          low: Math.max(0, old.low - riskCounts.LOW),
        }
      })
    },
  })
}
