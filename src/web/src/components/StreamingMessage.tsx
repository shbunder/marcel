import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  text: string
}

export const StreamingMessage = memo(function StreamingMessage({ text }: Props) {
  if (!text) return null

  return (
    <div className="message message--assistant message--streaming">
      <div className="message__content">
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
          {text}
        </ReactMarkdown>
        <span className="streaming-cursor" />
      </div>
    </div>
  )
})
