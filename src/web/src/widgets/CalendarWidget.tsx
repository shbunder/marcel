export interface CalendarEvent {
  date: string
  title: string
  time?: string
  location?: string
}

/**
 * Detect calendar-like content in Marcel's response format.
 *
 * Marcel outputs calendar events as structured markdown with:
 * - Date headers like "**📅 Today — Friday Apr 3**" or "**Monday Apr 6 onwards**"
 * - Event lines like "- 🪒 **Afspraak kapper** — 10:00–12:00 @ Location"
 * - Also detects markdown tables with date/event columns (legacy format).
 */
export function detectCalendar(content: string): CalendarEvent[] | null {
  // Try structured list format first (Marcel's actual output)
  const listEvents = parseCalendarList(content)
  if (listEvents && listEvents.length > 0) return listEvents

  // Fallback: markdown table format
  return parseCalendarTable(content)
}

// Match date header lines: "**📅 Today — Friday Apr 3**", "**Monday Apr 6 onwards**", etc.
const DATE_HEADER_RE =
  /\*{0,2}[📅🏕️📚]*\s*(?:Today\s*[—\-]\s*)?(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}/i

// Match event lines: "- 🪒 **Title** (optional) — 10:00–12:00 @ Location"
const EVENT_LINE_RE = /^[-•*]\s+(.+)/

// Extract time range: "10:00–12:00" or "16:00"
const TIME_RE = /(\d{1,2}:\d{2}(?:\s*[–\-]\s*\d{1,2}:\d{2})?)/

// Extract location after @
const LOCATION_RE = /@\s*(.+?)(?:\s*$|\s*\()/

// Strip markdown bold markers and emojis for clean titles
function cleanTitle(raw: string): string {
  return raw
    .replace(/\*{1,2}/g, '')
    .replace(/[📅🏕️📚🪒🏀👾🐣⚠️🎂🎉💊🧹🛒🔔💰🎯]/gu, '')
    .replace(/\s*[—\-]\s*\d{1,2}:\d{2}.*/, '') // strip time suffix
    .replace(/@\s*.+/, '') // strip location suffix
    .replace(/\(.*?\)/g, '') // strip parentheticals
    .trim()
}

function parseCalendarList(content: string): CalendarEvent[] | null {
  const lines = content.split('\n')
  const events: CalendarEvent[] = []
  let currentDate = ''

  for (const line of lines) {
    // Check for date header
    const dateMatch = line.match(DATE_HEADER_RE)
    if (dateMatch) {
      // Extract the date portion from the header
      currentDate = line
        .replace(/\*{1,2}/g, '')
        .replace(/[📅🏕️📚]/gu, '')
        .replace(/^\s*/, '')
        .replace(/\s*$/, '')
        .replace(/\s+onwards.*/, '')
        .replace(/\s+starts.*/, '')
        .trim()
      continue
    }

    // Check for event line under a date
    const eventMatch = line.match(EVENT_LINE_RE)
    if (eventMatch && currentDate) {
      const raw = eventMatch[1]
      const title = cleanTitle(raw)
      if (!title) continue

      const timeMatch = raw.match(TIME_RE)
      const locationMatch = raw.match(LOCATION_RE)

      events.push({
        date: currentDate,
        title,
        time: timeMatch?.[1],
        location: locationMatch?.[1]?.trim(),
      })
    }
  }

  return events.length >= 1 ? events : null
}

function parseCalendarTable(content: string): CalendarEvent[] | null {
  const lines = content.split('\n')
  const headerIdx = lines.findIndex((line) => {
    const lower = line.toLowerCase()
    return (
      line.includes('|') &&
      (lower.includes('date') || lower.includes('when') || lower.includes('time'))
    )
  })
  if (headerIdx < 0) return null

  const sepIdx = headerIdx + 1
  if (sepIdx >= lines.length || !/^\s*\|[\s-:|]+\|\s*$/.test(lines[sepIdx])) return null

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
  // Group events by date
  const grouped = new Map<string, CalendarEvent[]>()
  for (const event of events) {
    const existing = grouped.get(event.date) || []
    existing.push(event)
    grouped.set(event.date, existing)
  }

  return (
    <div className="widget widget--calendar">
      {[...grouped.entries()].map(([date, dateEvents]) => (
        <div key={date} className="calendar-day">
          <div className="calendar-day__header">{date}</div>
          {dateEvents.map((event, i) => (
            <div key={i} className="calendar-event">
              <div className="calendar-event__details">
                <div className="calendar-event__title">{event.title}</div>
                {event.time && <div className="calendar-event__time">{event.time}</div>}
                {event.location && <div className="calendar-event__location">{event.location}</div>}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
