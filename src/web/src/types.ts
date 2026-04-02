export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'error'
  content: string
  timestamp: number
}

export interface ActiveTool {
  id: string
  name: string
}

export interface ChatConfig {
  wsUrl: string
  user: string
  token: string
}

/** AG-UI event received from the WebSocket. */
export type ServerEvent =
  | { type: 'started'; conversation: string }
  | { type: 'text_message_start' }
  | { type: 'token'; text: string }
  | { type: 'text_message_end' }
  | { type: 'tool_call_start'; tool_call_id: string; tool_name: string }
  | { type: 'tool_call_end'; tool_call_id: string }
  | { type: 'tool_call_result'; tool_call_id: string; is_error: boolean; summary: string }
  | { type: 'done'; cost_usd?: number; turns?: number }
  | { type: 'error'; message: string }
