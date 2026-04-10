import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CalendarWidget, detectCalendar } from '../widgets/CalendarWidget'
import { ChecklistWidget, detectChecklist } from '../widgets/ChecklistWidget'
import type { Artifact } from '../types'

interface Props {
  artifactId: string
  initData: string
}

interface LegacyProps {
  conversationId: string
  initData: string
  turn?: string | null
}

/**
 * Render rich content from a fetched artifact or legacy message endpoint.
 */
function RichContent({ content, contentType }: { content: string; contentType?: string }) {
  if (contentType === 'image') {
    return (
      <div className="viewer__image">
        <img src={content} alt="Artifact" style={{ maxWidth: '100%', borderRadius: 8 }} />
      </div>
    )
  }

  // For calendar/checklist/markdown, detect widgets from content
  const calendarData = detectCalendar(content)
  const checklistData = detectChecklist(content)
  const hasWidget = calendarData || checklistData

  return (
    <>
      {calendarData && <CalendarWidget events={calendarData} />}
      {checklistData && <ChecklistWidget items={checklistData} />}
      {!hasWidget && (
        <div className="viewer__markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      )}
    </>
  )
}

/**
 * Artifact viewer: fetches a specific artifact by ID and renders
 * the appropriate widget.
 */
export function Viewer({ artifactId, initData }: Props) {
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const url = `/api/artifact/${encodeURIComponent(artifactId)}?initData=${encodeURIComponent(initData)}`
    fetch(url)
      .then((res) => {
        if (res.status === 401) throw new Error('Session expired — please reopen from Telegram')
        if (!res.ok) throw new Error(`Failed to load (${res.status})`)
        return res.json()
      })
      .then((data) => setArtifact(data))
      .catch((err) => setError(err.message))
  }, [artifactId, initData])

  if (error) {
    return (
      <div className="viewer">
        <div className="viewer__error">{error}</div>
      </div>
    )
  }

  if (!artifact) {
    return (
      <div className="viewer">
        <div className="viewer__loading">Loading…</div>
      </div>
    )
  }

  return (
    <div className="viewer">
      <div className="viewer__title">{artifact.title}</div>
      <RichContent content={artifact.content} contentType={artifact.content_type} />
    </div>
  )
}

/**
 * Legacy viewer: supports old "View in app" buttons that link to
 * ?conversation=X&turn=N. Fetches from the deprecated /api/message/ endpoint.
 */
export function LegacyViewer({ conversationId, initData, turn }: LegacyProps) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let url = `/api/message/${encodeURIComponent(conversationId)}?initData=${encodeURIComponent(initData)}`
    if (turn) {
      url += `&turn=${encodeURIComponent(turn)}`
    }
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load (${res.status})`)
        return res.json()
      })
      .then((data) => setContent(data.content))
      .catch((err) => setError(err.message))
  }, [conversationId, initData, turn])

  if (error) {
    return (
      <div className="viewer">
        <div className="viewer__error">{error}</div>
      </div>
    )
  }

  if (content === null) {
    return (
      <div className="viewer">
        <div className="viewer__loading">Loading…</div>
      </div>
    )
  }

  return (
    <div className="viewer">
      <RichContent content={content} />
    </div>
  )
}
