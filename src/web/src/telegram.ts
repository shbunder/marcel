/**
 * Telegram Mini App adapter.
 *
 * Wraps `window.Telegram.WebApp` (injected by the Telegram SDK script tag in
 * index.html) and provides a single detection point for Mini App mode.
 *
 * Returns `null` when running in a normal browser.
 */

export interface TelegramWebApp {
  initData: string
  initDataUnsafe: { user?: { id: number; first_name: string } }
  colorScheme: 'light' | 'dark'
  themeParams: Record<string, string>
  ready(): void
  close(): void
  expand(): void
  isExpanded: boolean
  BackButton: {
    show(): void
    hide(): void
    onClick(cb: () => void): void
    offClick(cb: () => void): void
  }
}

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp }
  }
}

/**
 * Return the Telegram WebApp bridge if running inside a Mini App, else `null`.
 *
 * Detection relies on `initData` being a non-empty string — this is only set
 * when Telegram's WebView injects the SDK.
 */
export function getTelegramWebApp(): TelegramWebApp | null {
  const tg = window.Telegram?.WebApp
  if (!tg || !tg.initData) return null
  return tg
}
