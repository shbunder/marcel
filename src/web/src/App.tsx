import { useEffect } from 'react'
import { Chat } from './components/Chat'
import { Viewer } from './components/Viewer'
import { useChat } from './hooks/useChat'
import { getTelegramWebApp } from './telegram'
import type { ChatConfig } from './types'

const tg = getTelegramWebApp()

// Check if we're in viewer mode (opened from "Show events" button with a
// conversation ID in the URL).
const searchParams = new URLSearchParams(location.search)
const viewConversation = searchParams.get('conversation')
const viewTurn = searchParams.get('turn')

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
  // In viewer mode we don't need the WebSocket chat — just render the widget.
  // useChat is still called (hooks must be unconditional) but won't connect
  // if we never call sendMessage.
  const { messages, streamingText, activeTools, isConnected, sendMessage, startNewConversation } =
    useChat(viewConversation ? { ...config, wsUrl: '' } : config)

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

  // Wire Telegram back button — in viewer mode, always show and close on tap
  useEffect(() => {
    if (!tg) return
    if (viewConversation) {
      tg.BackButton.show()
      const handler = () => tg.close()
      tg.BackButton.onClick(handler)
      return () => tg.BackButton.offClick(handler)
    }
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

  // Viewer mode: render the widget directly from the conversation
  if (viewConversation && tg?.initData) {
    return (
      <div className="app">
        <Viewer conversationId={viewConversation} initData={tg.initData} turn={viewTurn} />
      </div>
    )
  }

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
