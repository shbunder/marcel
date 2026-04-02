import { useState } from 'react'

export interface ChecklistItem {
  text: string
  checked: boolean
}

/**
 * Detect GFM task list patterns (- [ ] / - [x]).
 * Returns parsed items or null if fewer than 2 items found.
 */
export function detectChecklist(content: string): ChecklistItem[] | null {
  const pattern = /^- \[([ xX])\] (.+)$/gm
  const items: ChecklistItem[] = []
  let match: RegExpExecArray | null

  while ((match = pattern.exec(content)) !== null) {
    items.push({
      checked: match[1].toLowerCase() === 'x',
      text: match[2],
    })
  }

  return items.length >= 2 ? items : null
}

interface Props {
  items: ChecklistItem[]
}

export function ChecklistWidget({ items: initial }: Props) {
  const [items, setItems] = useState(initial)

  const toggle = (index: number) => {
    setItems((prev) => prev.map((item, i) => (i === index ? { ...item, checked: !item.checked } : item)))
  }

  return (
    <div className="widget widget--checklist">
      {items.map((item, i) => (
        <label key={i} className="checklist-item">
          <input
            type="checkbox"
            checked={item.checked}
            onChange={() => toggle(i)}
            className="checklist-item__checkbox"
          />
          <span className={`checklist-item__text ${item.checked ? 'checklist-item__text--done' : ''}`}>
            {item.text}
          </span>
        </label>
      ))}
    </div>
  )
}
