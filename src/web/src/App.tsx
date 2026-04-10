import { useCallback, useEffect, useState } from 'react'
import { Gallery } from './components/Gallery'
import { LegacyViewer, Viewer } from './components/Viewer'
import { getTelegramWebApp } from './telegram'

const tg = getTelegramWebApp()

// Parse URL params to determine initial view
const searchParams = new URLSearchParams(location.search)
const initialArtifact = searchParams.get('artifact')
const initialConversation = searchParams.get('conversation')
const initialTurn = searchParams.get('turn')

// Telegram Mini App initialization (runs once at load)
if (tg) {
  document.body.classList.add('tg-mini-app')

  const bg = tg.themeParams.bg_color || '#1a1a2e'
  const secondaryBg = tg.themeParams.secondary_bg_color || '#16213e'
  tg.setHeaderColor(bg)
  tg.setBackgroundColor(bg)
  if (tg.isVersionAtLeast('7.10')) {
    tg.setBottomBarColor(secondaryBg)
  }

  if (tg.isVersionAtLeast('7.7')) {
    tg.disableVerticalSwipes()
  }

  tg.expand()
  tg.ready()
}

type View =
  | { mode: 'gallery' }
  | { mode: 'viewer'; artifactId: string }
  | { mode: 'legacy'; conversationId: string; turn: string | null }

function getInitialView(): View {
  if (initialArtifact) {
    return { mode: 'viewer', artifactId: initialArtifact }
  }
  if (initialConversation && initialTurn !== undefined) {
    return { mode: 'legacy', conversationId: initialConversation, turn: initialTurn }
  }
  return { mode: 'gallery' }
}

export function App() {
  const [view, setView] = useState<View>(getInitialView)

  const navigateToArtifact = useCallback((id: string) => {
    setView({ mode: 'viewer', artifactId: id })
  }, [])

  const navigateToGallery = useCallback(() => {
    setView({ mode: 'gallery' })
  }, [])

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

    if (view.mode === 'gallery') {
      // Gallery is root — back closes the app
      tg.BackButton.show()
      const handler = () => tg.close()
      tg.BackButton.onClick(handler)
      return () => tg.BackButton.offClick(handler)
    }

    // Viewer/legacy — back goes to gallery (if opened from menu button)
    // or closes (if opened from inline button directly)
    tg.BackButton.show()
    const handler = () => {
      if (initialArtifact || (initialConversation && initialTurn !== undefined)) {
        // Opened from inline button — back closes
        tg.close()
      } else {
        // Opened from menu button, navigated to artifact — back goes to gallery
        navigateToGallery()
      }
    }
    tg.BackButton.onClick(handler)
    return () => tg.BackButton.offClick(handler)
  }, [view, navigateToGallery])

  const initData = tg?.initData || ''

  return (
    <div className="app">
      {view.mode === 'viewer' && initData && <Viewer artifactId={view.artifactId} initData={initData} />}

      {view.mode === 'legacy' && initData && (
        <LegacyViewer conversationId={view.conversationId} initData={initData} turn={view.turn} />
      )}

      {view.mode === 'gallery' && initData && (
        <Gallery initData={initData} conversationId={initialConversation} onSelectArtifact={navigateToArtifact} />
      )}

      {!initData && (
        <div className="viewer">
          <div className="viewer__error">This app requires Telegram Mini App context.</div>
        </div>
      )}
    </div>
  )
}
