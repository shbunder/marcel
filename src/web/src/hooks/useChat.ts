import { useCallback, useEffect, useRef, useState } from 'react'
import type { ActiveTool, ChatConfig, Message, ServerEvent } from '../types'

let nextId = 0
function genId(): string {
  return `msg-${Date.now()}-${++nextId}`
}

export function useChat(config: ChatConfig) {
  const [messages, setMessages] = useState<Message[]>([])
  const [streamingText, setStreamingText] = useState('')
  const [activeTools, setActiveTools] = useState<ActiveTool[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [lastCost, setLastCost] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const streamBuf = useRef('')
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const reconnectDelay = useRef(1000)
  const conversationRef = useRef<string | null>(null)

  // Keep ref in sync for use in callbacks
  conversationRef.current = conversationId

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(config.wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setIsConnected(true)
      setError(null)
      reconnectDelay.current = 1000
    }

    ws.onclose = () => {
      setIsConnected(false)
      wsRef.current = null
      // Auto-reconnect with exponential backoff
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(reconnectDelay.current * 2, 30000)
        connect()
      }, reconnectDelay.current)
    }

    ws.onerror = () => {
      setError('Connection error')
    }

    ws.onmessage = (ev) => {
      let event: ServerEvent
      try {
        event = JSON.parse(ev.data)
      } catch {
        return
      }

      switch (event.type) {
        case 'started':
          setConversationId(event.conversation)
          conversationRef.current = event.conversation
          break

        case 'text_message_start':
          streamBuf.current = ''
          setStreamingText('')
          break

        case 'token':
          streamBuf.current += event.text
          setStreamingText(streamBuf.current)
          break

        case 'text_message_end':
          if (streamBuf.current) {
            const text = streamBuf.current
            setMessages((prev) => [
              ...prev,
              { id: genId(), role: 'assistant', content: text, timestamp: Date.now() },
            ])
          }
          streamBuf.current = ''
          setStreamingText('')
          break

        case 'tool_call_start':
          setActiveTools((prev) => [...prev, { id: event.tool_call_id, name: event.tool_name }])
          break

        case 'tool_call_end':
          setActiveTools((prev) => prev.filter((t) => t.id !== event.tool_call_id))
          break

        case 'done':
          setLastCost(event.cost_usd ?? null)
          setActiveTools([])
          setStreamingText('')
          streamBuf.current = ''
          break

        case 'error':
          setError(event.message)
          setMessages((prev) => [
            ...prev,
            { id: genId(), role: 'error', content: event.message, timestamp: Date.now() },
          ])
          setActiveTools([])
          setStreamingText('')
          streamBuf.current = ''
          break
      }
    }
  }, [config.wsUrl])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendMessage = useCallback(
    (text: string) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

      // Optimistic: add user message immediately
      setMessages((prev) => [
        ...prev,
        { id: genId(), role: 'user', content: text, timestamp: Date.now() },
      ])

      wsRef.current.send(
        JSON.stringify({
          token: config.token,
          text,
          user: config.user,
          conversation: conversationRef.current,
          channel: 'app',
        }),
      )
    },
    [config.token, config.user],
  )

  const startNewConversation = useCallback(() => {
    setMessages([])
    setConversationId(null)
    conversationRef.current = null
    setStreamingText('')
    setActiveTools([])
    setLastCost(null)
    setError(null)
    streamBuf.current = ''
  }, [])

  return {
    messages,
    streamingText,
    activeTools,
    conversationId,
    isConnected,
    lastCost,
    error,
    sendMessage,
    startNewConversation,
  }
}
