import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CalendarWidget, detectCalendar } from '../widgets/CalendarWidget'
import { ChecklistWidget, detectChecklist } from '../widgets/ChecklistWidget'

interface Props {
  conversationId: string
  initData: string
}

/**
 * Viewer mode: fetches the last assistant message from a conversation and
 * renders it as a rich widget. Used when the Mini App is opened from a
 * "Show events" / "Show checklist" inline button in Telegram.
 */
export function Viewer({ conversationId, initData }: Props) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const url = `/api/message/${encodeURIComponent(conversationId)}?initData=${encodeURIComponent(initData)}`
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load (${res.status})`)
        return res.json()
      })
      .then((data) => setContent(data.content))
      .catch((err) => setError(err.message))
  }, [conversationId, initData])

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

  const calendarData = detectCalendar(content)
  const checklistData = detectChecklist(content)
  const hasWidget = calendarData || checklistData

  return (
    <div className="viewer">
      {calendarData && <CalendarWidget events={calendarData} />}
      {checklistData && <ChecklistWidget items={checklistData} />}
      {!hasWidget && (
        <div className="viewer__markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}
