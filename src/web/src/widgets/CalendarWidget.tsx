export interface CalendarEvent {
  date: string
  title: string
  time?: string
  location?: string
}

/**
 * Detect calendar-like content in Marcel's response format.
 *
 * Marcel outputs calendar events in varied formats:
 * - Date headers: "**📅 Today — Friday Apr 3**", "**📅 Saturday 4 & Sunday 5 April**"
 * - Bullet events: "- 🪒 **Afspraak kapper** — 10:00–12:00 @ Location"
 * - Paragraph events: "🏕 **Weekend VdB** *(Kids)*\nDescription with **Friday 16:00**"
 * - Also detects markdown tables with date/event columns (legacy format).
 */
export function detectCalendar(content: string): CalendarEvent[] | null {
  const listEvents = parseCalendarList(content)
  if (listEvents && listEvents.length > 0) return listEvents
  return parseCalendarTable(content)
}

// Date header patterns — very flexible to handle Marcel's varied formats:
// "📅 Today — Friday Apr 3", "Saturday 4 & Sunday 5 April", "Weekend Apr 4–5",
// "Monday Apr 6 onwards", "Ongoing all week"
const DATE_HEADER_RE = new RegExp(
  [
    // Calendar/event emoji at start of line (strong signal)
    '(?:^|\\n)\\s*\\*{0,2}[📅🗓🏕📚🏖]*\\s*',
    '(?:',
    // Pattern A: "Today — Friday Apr 3" or "Saturday 4 & Sunday 5 April"
    '(?:Today|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\\w*)[\\s,]*',
    '(?:\\d{1,2}[\\s&,–\\-]*)*',
    '(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\w*)?',
    '|',
    // Pattern B: "Apr 4–5", "April 3"
    '(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\w*\\s+\\d{1,2}',
    '|',
    // Pattern C: "Ongoing all week", "Also still running"
    '(?:Ongoing|Also\\s+still)',
    ')',
  ].join(''),
  'i',
)

// Event lines: bullets "- 🪒 **Title**" or emoji-started "🏕 **Title**"
const EVENT_LINE_RE = /^(?:[-•*]\s+|[📅🗓🏕📚🪒🏀👾🐣⚠🚫🎂🎉💊🧹🛒🔔💰🎯🏖]\s*)(.+)/u

// Extract time: "10:00–12:00", "16:00", "Friday 16:00", "Sunday 10:00"
const TIME_RE = /(\d{1,2}:\d{2}(?:\s*[–\-]\s*\d{1,2}:\d{2})?)/

// Extract location after @
const LOCATION_RE = /@\s*(.+?)(?:\s*$|\s*\()/

// Strip markdown, emojis, times, locations for clean title
function cleanTitle(raw: string): string {
  return raw
    .replace(/\*{1,2}/g, '')
    .replace(/\p{Emoji_Presentation}/gu, '')
    .replace(/\s*[—\-]\s*\d{1,2}:\d{2}[^,\n]*/, '') // strip time suffix
    .replace(/(?:from|until|starts?|through)\s+\*{0,2}\w+\s+\d{1,2}:\d{2}\*{0,2}/gi, '') // "from Friday 16:00"
    .replace(/@\s*.+/, '') // strip location suffix
    .replace(/\(.*?\)/g, '') // strip parentheticals like *(Kids)*
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function parseCalendarList(content: string): CalendarEvent[] | null {
  const lines = content.split('\n')
  const events: CalendarEvent[] = []
  let currentDate = ''

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    // Check for date header
    if (DATE_HEADER_RE.test(line)) {
      currentDate = line
        .replace(/\*{1,2}/g, '')
        .replace(/\p{Emoji_Presentation}/gu, '')
        .replace(/^\s*/, '')
        .replace(/\s*$/, '')
        .replace(/\s+onwards.*$/i, '')
        .replace(/\s+starts.*$/i, '')
        .trim()
      // Don't skip — the line might also be an event (e.g. "🏕 **Weekend VdB** starts — 16:00")
      // If it has a bold title pattern, fall through to event detection below
      if (!/\*{2}.+\*{2}/.test(line)) continue
    }

    // Check for event line (bullet or emoji-started)
    const eventMatch = line.match(EVENT_LINE_RE)
    if (eventMatch && currentDate) {
      const raw = eventMatch[1]
      const title = cleanTitle(raw)
      if (!title || title.length < 3) continue

      // Collect times from this line and the next line(s) of the same block
      let fullText = raw
      // Look ahead for continuation lines (not a new bullet or header)
      for (let j = i + 1; j < lines.length; j++) {
        const next = lines[j]
        if (!next.trim() || EVENT_LINE_RE.test(next) || DATE_HEADER_RE.test(next)) break
        if (/^[-—]/.test(next.trim())) break
        fullText += ' ' + next.trim()
        i = j // skip these lines
      }

      const timeMatch = fullText.match(TIME_RE)
      const locationMatch = fullText.match(LOCATION_RE)

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
