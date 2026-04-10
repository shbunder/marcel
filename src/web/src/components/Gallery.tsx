import { useEffect, useState } from 'react'
import type { ArtifactSummary } from '../types'

const CONTENT_TYPE_ICONS: Record<string, string> = {
  calendar: '📅',
  checklist: '☑️',
  markdown: '📝',
  image: '🖼️',
  chart_data: '📊',
  html: '🌐',
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 7) return `${diffDays}d ago`
  return d.toLocaleDateString()
}

interface Props {
  initData: string
  conversationId?: string | null
  onSelectArtifact: (id: string) => void
}

export function Gallery({ initData, conversationId, onSelectArtifact }: Props) {
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let url = `/api/artifacts?initData=${encodeURIComponent(initData)}`
    if (conversationId) {
      url += `&conversation=${encodeURIComponent(conversationId)}`
    }
    fetch(url)
      .then((res) => {
        if (res.status === 401) throw new Error('Session expired — please reopen from Telegram')
        if (!res.ok) throw new Error(`Failed to load (${res.status})`)
        return res.json()
      })
      .then((data) => {
        setArtifacts(data.artifacts)
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message)
        setLoading(false)
      })
  }, [initData, conversationId])

  if (error) {
    return (
      <div className="gallery">
        <div className="gallery__error">{error}</div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="gallery">
        <div className="gallery__loading">Loading…</div>
      </div>
    )
  }

  if (artifacts.length === 0) {
    return (
      <div className="gallery">
        <div className="gallery__empty">
          <div className="gallery__empty-icon">✨</div>
          <div className="gallery__empty-text">No rich content yet</div>
          <div className="gallery__empty-hint">
            When Marcel sends calendars, checklists, or other rich content, it will appear here.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="gallery">
      <div className="gallery__header">Rich Content</div>
      <div className="gallery__list">
        {artifacts.map((a) => (
          <button key={a.id} className="gallery__card" onClick={() => onSelectArtifact(a.id)}>
            <span className="gallery__card-icon">{CONTENT_TYPE_ICONS[a.content_type] || '📄'}</span>
            <div className="gallery__card-body">
              <div className="gallery__card-title">{a.title}</div>
              <div className="gallery__card-date">{formatDate(a.created_at)}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
