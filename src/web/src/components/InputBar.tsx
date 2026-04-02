import { useCallback, useRef, type KeyboardEvent } from 'react'

interface Props {
  onSend: (text: string) => void
  disabled?: boolean
}

export function InputBar({ onSend, disabled }: Props) {
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        const text = inputRef.current?.value.trim()
        if (text) {
          onSend(text)
          if (inputRef.current) inputRef.current.value = ''
        }
      }
    },
    [onSend],
  )

  return (
    <div className="input-bar">
      <textarea
        ref={inputRef}
        className="input-bar__textarea"
        placeholder="Message Marcel..."
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={1}
        autoFocus
      />
      <button
        className="input-bar__send"
        onClick={() => {
          const text = inputRef.current?.value.trim()
          if (text) {
            onSend(text)
            if (inputRef.current) inputRef.current.value = ''
          }
        }}
        disabled={disabled}
      >
        Send
      </button>
    </div>
  )
}
