import { useEffect, useRef } from 'react'
import type { ActiveTool, Message } from '../types'
import { InputBar } from './InputBar'
import { MessageBubble } from './MessageBubble'
import { StreamingMessage } from './StreamingMessage'
import { ToolIndicator } from './ToolIndicator'

interface Props {
  messages: Message[]
  streamingText: string
  activeTools: ActiveTool[]
  isConnected: boolean
  onSend: (text: string) => void
}

export function Chat({ messages, streamingText, activeTools, isConnected, onSend }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new content
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, streamingText, activeTools])

  return (
    <div className="chat">
      <div className="chat__messages" ref={scrollRef}>
        {messages.length === 0 && !streamingText && (
          <div className="chat__empty">Start a conversation with Marcel</div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <StreamingMessage text={streamingText} />
        <ToolIndicator tools={activeTools} />
      </div>
      <InputBar onSend={onSend} disabled={!isConnected} />
    </div>
  )
}
