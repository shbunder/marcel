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

// Telegram Mini App initialization (runs once at load)
if (tg) {
  document.body.classList.add('tg-mini-app')

  // Match header/background/bottom bar to our dark theme, falling back to
  // Telegram's own theme colors when available.
  const bg = tg.themeParams.bg_color || '#1a1a2e'
  const secondaryBg = tg.themeParams.secondary_bg_color || '#16213e'
  tg.setHeaderColor(bg)
  tg.setBackgroundColor(bg)
  if (tg.isVersionAtLeast('7.10')) {
    tg.setBottomBarColor(secondaryBg)
  }

  // Prevent vertical swipes from accidentally closing the sheet while the
  // user scrolls through the chat.
  if (tg.isVersionAtLeast('7.7')) {
    tg.disableVerticalSwipes()
  }

  tg.expand()
  tg.ready()
}

export function App() {
  const { messages, streamingText, activeTools, isConnected, sendMessage, startNewConversation } =
    useChat(config)

  // Listen for live theme changes (user toggles dark/light mode in Telegram)
  useEffect(() => {
    if (!tg) return
    const onThemeChanged = () => {
      const bg = tg.themeParams.bg_color || '#1a1a2e'
      const secondaryBg = tg.themeParams.secondary_bg_color || '#16213e'
      tg.setHeaderColor(bg)
      tg.setBackgroundColor(bg)
      if (tg.isVersionAtLeast('7.10')) {
        tg.setBottomBarColor(secondaryBg)
      }
    }
    tg.onEvent('themeChanged', onThemeChanged)
    return () => tg.offEvent('themeChanged', onThemeChanged)
  }, [])

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
