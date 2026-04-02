import { Chat } from './components/Chat'
import { useChat } from './hooks/useChat'
import type { ChatConfig } from './types'

const config: ChatConfig = {
  wsUrl: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`,
  user: localStorage.getItem('marcel_user') || 'web',
  token: localStorage.getItem('marcel_token') || '',
}

export function App() {
  const { messages, streamingText, activeTools, isConnected, sendMessage, startNewConversation } =
    useChat(config)

  return (
    <div className="app">
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
