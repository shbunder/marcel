import { useEffect } from 'react'
import { Chat } from './components/Chat'
import { useChat } from './hooks/useChat'
import { getTelegramWebApp } from './telegram'
import type { ChatConfig } from './types'

const tg = getTelegramWebApp()

const config: ChatConfig = {
  wsUrl: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`,
  user: tg ? 'tg' : localStorage.getItem('marcel_user') || 'web',
  token: tg ? '' : localStorage.getItem('marcel_token') || '',
  initData: tg?.initData,
}

// Apply Telegram Mini App body class for CSS overrides
if (tg) {
  document.body.classList.add('tg-mini-app')
  tg.ready()
  tg.expand()
}

export function App() {
  const { messages, streamingText, activeTools, isConnected, sendMessage, startNewConversation } =
    useChat(config)

  // Wire Telegram back button
  useEffect(() => {
    if (!tg) return
    const hasMessages = messages.length > 0 || streamingText.length > 0
    if (hasMessages) {
      tg.BackButton.show()
    } else {
      tg.BackButton.hide()
    }
    const handler = () => {
      if (messages.length > 0) {
        startNewConversation()
      } else {
        tg.close()
      }
    }
    tg.BackButton.onClick(handler)
    return () => tg.BackButton.offClick(handler)
  }, [messages, streamingText, startNewConversation])

  return (
    <div className="app">
      {!tg && (
        <header className="app__header">
          <span className="app__title">Marcel</span>
          <div className="app__status">
            <span className={`app__status-dot ${isConnected ? 'app__status-dot--on' : 'app__status-dot--off'}`} />
            {isConnected ? 'Connected' : 'Disconnected'}
          </div>
          <button className="app__new-btn" onClick={startNewConversation}>
            New chat
          </button>
        </header>
      )}
      <Chat
        messages={messages}
        streamingText={streamingText}
        activeTools={activeTools}
        isConnected={isConnected}
        onSend={sendMessage}
      />
    </div>
  )
}
