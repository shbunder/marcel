import React, { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { A2UIRenderer } from './A2UIRenderer'
import { CalendarWidget, detectCalendar } from '../widgets/CalendarWidget'
import { ChecklistWidget, detectChecklist } from '../widgets/ChecklistWidget'
import type { Artifact, ComponentSchema } from '../types'

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
 * Native widget registry — maps A2UI component names to existing widgets.
 * When an A2UI artifact matches a key here, the native widget is used instead
 * of the generic renderer (top of the fallback chain).
 */
const NATIVE_WIDGETS: Record<string, (props: Record<string, unknown>) => React.ReactElement | null> = {
  calendar: (props) => {
    const events = props.events as Array<{ date: string; title: string; time?: string; location?: string }>
    return events ? <CalendarWidget events={events} /> : null
  },
  checklist: (props) => {
    const items = props.items as Array<{ text: string; checked: boolean }>
    return items ? <ChecklistWidget items={items} /> : null
  },
}

/**
 * Render rich content from a fetched artifact or legacy message endpoint.
 *
 * Fallback chain for A2UI:
 *   Native widget (CalendarWidget, ChecklistWidget, ...)
 *     → Generic A2UI renderer (auto-generated from schema)
 *       → Raw markdown (last resort)
 */
function RichContent({
  content,
  contentType,
  componentName,
  componentSchema,
}: {
  content: string
  contentType?: string
  componentName?: string
  componentSchema?: ComponentSchema | null
}) {
  // A2UI content type — use the fallback chain
  if (contentType === 'a2ui' && componentName) {
    try {
      const props = JSON.parse(content) as Record<string, unknown>

      // 1. Try native widget
      const NativeWidget = NATIVE_WIDGETS[componentName]
      if (NativeWidget) {
        const rendered = NativeWidget(props)
        if (rendered) return rendered
      }

      // 2. Generic A2UI renderer (if we have the schema)
      if (componentSchema) {
        return <A2UIRenderer componentName={componentName} schema={componentSchema} props={props} />
      }

      // 3. Fallback: render props as formatted JSON
      return (
        <div className="viewer__markdown">
          <pre>{JSON.stringify(props, null, 2)}</pre>
        </div>
      )
    } catch {
      // Invalid JSON — fall through to markdown
    }
  }

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
  const [componentSchema, setComponentSchema] = useState<ComponentSchema | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const url = `/api/artifact/${encodeURIComponent(artifactId)}?initData=${encodeURIComponent(initData)}`
    fetch(url)
      .then((res) => {
        if (res.status === 401) throw new Error('Session expired — please reopen from Telegram')
        if (!res.ok) throw new Error(`Failed to load (${res.status})`)
        return res.json()
      })
      .then((data: Artifact) => {
        setArtifact(data)
        // Fetch component schema for A2UI artifacts
        if (data.content_type === 'a2ui' && data.component_name) {
          const schemaUrl = `/api/components/${encodeURIComponent(data.component_name)}?initData=${encodeURIComponent(initData)}`
          fetch(schemaUrl)
            .then((res) => (res.ok ? res.json() : null))
            .then((schema) => setComponentSchema(schema))
            .catch(() => {}) // Schema is optional — fallback still works
        }
      })
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
      <RichContent
        content={artifact.content}
        contentType={artifact.content_type}
        componentName={artifact.component_name}
        componentSchema={componentSchema}
      />
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
