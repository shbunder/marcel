import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../types'
import { CalendarWidget, detectCalendar } from '../widgets/CalendarWidget'
import { ChecklistWidget, detectChecklist } from '../widgets/ChecklistWidget'

interface Props {
  message: Message
}

export function MessageBubble({ message }: Props) {
  if (message.role === 'user') {
    return (
      <div className="message message--user">
        <div className="message__content">{message.content}</div>
      </div>
    )
  }

  if (message.role === 'error') {
    return (
      <div className="message message--error">
        <div className="message__content">{message.content}</div>
      </div>
    )
  }

  if (message.role === 'system') {
    return (
      <div className="message message--system">
        <div className="message__content">{message.content}</div>
      </div>
    )
  }

  // Assistant message — check for widget patterns
  const calendarData = detectCalendar(message.content)
  const checklistData = detectChecklist(message.content)

  return (
    <div className="message message--assistant">
      <div className="message__content">
        {calendarData && <CalendarWidget events={calendarData} />}
        {checklistData && <ChecklistWidget items={checklistData} />}
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ),
          }}
        >
          {message.content}
        </ReactMarkdown>
      </div>
    </div>
  )
}
