export interface SharedFile {
  id: string
  drive_id: string
  item_id: string
  risk_score: number
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW'
  source: string
  item_type: string
  item_path: string
  item_web_url: string
  sharing_type: string
  shared_with: string
  shared_with_type: string
}

export interface FilesResponse {
  files: SharedFile[]
  last_scan: string | null
  scan_status: 'completed' | 'running' | null
}

export interface StatsResponse {
  total: number
  high: number
  medium: number
  low: number
  last_scan: string | null
  scan_status: 'completed' | 'running' | null
}

export type UnshareReason = 'ACCESS_DENIED' | 'NOT_FOUND' | 'THROTTLED' | 'VERIFICATION_FAILED' | 'UNKNOWN'

export interface UnshareFailure {
  id: string
  reason: UnshareReason
  message: string
  action: string
}

export interface UnshareResponse {
  succeeded: string[]
  failed: UnshareFailure[]
}
