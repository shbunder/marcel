import type { ActiveTool } from '../types'

interface Props {
  tools: ActiveTool[]
}

export function ToolIndicator({ tools }: Props) {
  if (tools.length === 0) return null

  return (
    <div className="tool-indicator">
      {tools.map((tool) => (
        <div key={tool.id} className="tool-indicator__item">
          <span className="tool-indicator__icon">&#9881;</span>
          <span className="tool-indicator__name">{tool.name}</span>
          <span className="tool-indicator__dots">&hellip;</span>
        </div>
      ))}
    </div>
  )
}
