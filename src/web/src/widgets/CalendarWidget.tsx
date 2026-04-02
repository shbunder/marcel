export interface CalendarEvent {
  date: string
  title: string
  time?: string
  location?: string
}

/**
 * Detect calendar-like markdown tables.
 * Looks for tables with columns matching date-related headers.
 * Returns parsed events or null if no calendar pattern detected.
 */
export function detectCalendar(content: string): CalendarEvent[] | null {
  const lines = content.split('\n')
  // Find table header row with date-like column
  const headerIdx = lines.findIndex((line) => {
    const lower = line.toLowerCase()
    return (
      line.includes('|') &&
      (lower.includes('date') || lower.includes('when') || lower.includes('time'))
    )
  })
  if (headerIdx < 0) return null

  // Check for separator row
  const sepIdx = headerIdx + 1
  if (sepIdx >= lines.length || !/^\s*\|[\s-:|]+\|\s*$/.test(lines[sepIdx])) return null

  // Parse header columns
  const headers = lines[headerIdx]
    .split('|')
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean)

  const dateCol = headers.findIndex((h) => h === 'date' || h === 'when' || h === 'start')
  const titleCol = headers.findIndex(
    (h) => h === 'event' || h === 'title' || h === 'name' || h === 'what' || h === 'description',
  )
  const timeCol = headers.findIndex((h) => h === 'time' || h === 'start time' || h === 'hour')
  const locationCol = headers.findIndex((h) => h === 'location' || h === 'where' || h === 'place')

  if (dateCol < 0 || titleCol < 0) return null

  // Parse data rows
  const events: CalendarEvent[] = []
  for (let i = sepIdx + 1; i < lines.length; i++) {
    const line = lines[i]
    if (!line.includes('|')) break
    const cols = line
      .split('|')
      .map((c) => c.trim())
      .filter(Boolean)
    if (cols.length <= Math.max(dateCol, titleCol)) continue

    events.push({
      date: cols[dateCol],
      title: cols[titleCol],
      time: timeCol >= 0 ? cols[timeCol] : undefined,
      location: locationCol >= 0 ? cols[locationCol] : undefined,
    })
  }

  return events.length >= 1 ? events : null
}

interface Props {
  events: CalendarEvent[]
}

export function CalendarWidget({ events }: Props) {
  return (
    <div className="widget widget--calendar">
      {events.map((event, i) => (
        <div key={i} className="calendar-event">
          <div className="calendar-event__date">{event.date}</div>
          <div className="calendar-event__details">
            <div className="calendar-event__title">{event.title}</div>
            {event.time && <div className="calendar-event__time">{event.time}</div>}
            {event.location && <div className="calendar-event__location">{event.location}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}
