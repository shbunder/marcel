/**
 * Generic A2UI renderer — auto-generates UI from a JSON Schema definition.
 *
 * Handles the common patterns:
 * - object with properties → labeled card with rows
 * - array of objects → table
 * - array of primitives → list
 * - primitive values → formatted text
 *
 * This is the fallback renderer for components that don't have a native
 * widget implementation.  It produces readable (not beautiful) output from
 * any valid A2UI payload.
 */

interface A2UIRendererProps {
  componentName: string
  schema: { props: Record<string, unknown> }
  props: Record<string, unknown>
}

export function A2UIRenderer({ componentName, schema, props }: A2UIRendererProps) {
  return (
    <div className="a2ui" data-component={componentName}>
      <RenderValue value={props} schema={schema.props} depth={0} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Internal renderers
// ---------------------------------------------------------------------------

interface RenderValueProps {
  value: unknown
  schema: Record<string, unknown>
  depth: number
  label?: string
}

function RenderValue({ value, schema, depth, label }: RenderValueProps) {
  if (value === null || value === undefined) return null

  const schemaType = schema?.type as string | undefined

  // Array → table or list
  if (Array.isArray(value)) {
    return <RenderArray items={value} schema={schema} label={label} />
  }

  // Object → labeled rows
  if (typeof value === 'object' && !Array.isArray(value)) {
    return (
      <RenderObject
        obj={value as Record<string, unknown>}
        schema={schema}
        depth={depth}
        label={label}
      />
    )
  }

  // Boolean
  if (typeof value === 'boolean') {
    return (
      <span className="a2ui__primitive">
        {label && <span className="a2ui__label">{label}: </span>}
        {value ? '✓' : '✗'}
      </span>
    )
  }

  // Number — format with locale
  if (typeof value === 'number') {
    const formatted =
      schemaType === 'number' || schemaType === 'integer'
        ? value.toLocaleString()
        : String(value)
    return (
      <span className="a2ui__primitive">
        {label && <span className="a2ui__label">{label}: </span>}
        {formatted}
      </span>
    )
  }

  // String (default)
  return (
    <span className="a2ui__primitive">
      {label && <span className="a2ui__label">{label}: </span>}
      {String(value)}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Array renderer — table for arrays of objects, list otherwise
// ---------------------------------------------------------------------------

interface RenderArrayProps {
  items: unknown[]
  schema: Record<string, unknown>
  label?: string
}

function RenderArray({ items, schema, label }: RenderArrayProps) {
  if (items.length === 0) {
    return label ? (
      <div className="a2ui__empty">
        <span className="a2ui__label">{label}: </span>
        <em>empty</em>
      </div>
    ) : null
  }

  const itemSchema = (schema?.items as Record<string, unknown>) ?? {}
  const itemType = itemSchema?.type as string | undefined

  // Array of objects → render as table
  if (itemType === 'object' || (typeof items[0] === 'object' && items[0] !== null)) {
    const itemProps = (itemSchema?.properties as Record<string, Record<string, unknown>>) ?? {}
    const columns = Object.keys(itemProps)

    // If we can't determine columns from schema, derive from first item
    const effectiveColumns =
      columns.length > 0
        ? columns
        : Object.keys(items[0] as Record<string, unknown>)

    return (
      <div className="a2ui__table-container">
        {label && <div className="a2ui__section-label">{label}</div>}
        <table className="a2ui__table">
          <thead>
            <tr>
              {effectiveColumns.map((col) => (
                <th key={col}>{formatLabel(col)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => {
              const row = item as Record<string, unknown>
              return (
                <tr key={i}>
                  {effectiveColumns.map((col) => (
                    <td key={col}>{formatCellValue(row[col], itemProps[col])}</td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  // Array of primitives → list
  return (
    <div className="a2ui__list">
      {label && <div className="a2ui__section-label">{label}</div>}
      <ul>
        {items.map((item, i) => (
          <li key={i}>{String(item)}</li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Object renderer — labeled rows for each property
// ---------------------------------------------------------------------------

interface RenderObjectProps {
  obj: Record<string, unknown>
  schema: Record<string, unknown>
  depth: number
  label?: string
}

function RenderObject({ obj, schema, depth, label }: RenderObjectProps) {
  const properties = (schema?.properties as Record<string, Record<string, unknown>>) ?? {}
  const keys = Object.keys(properties).length > 0 ? Object.keys(properties) : Object.keys(obj)

  // Don't nest too deep — show as JSON
  if (depth > 3) {
    return (
      <pre className="a2ui__json">
        {label && <span className="a2ui__label">{label}: </span>}
        {JSON.stringify(obj, null, 2)}
      </pre>
    )
  }

  return (
    <div className="a2ui__object">
      {label && <div className="a2ui__section-label">{label}</div>}
      {keys.map((key) => {
        const propSchema = properties[key] ?? {}
        const propValue = obj[key]
        if (propValue === undefined || propValue === null) return null

        // For nested objects/arrays, recurse
        if (typeof propValue === 'object') {
          return (
            <div key={key} className="a2ui__nested">
              <RenderValue
                value={propValue}
                schema={propSchema}
                depth={depth + 1}
                label={formatLabel(key)}
              />
            </div>
          )
        }

        // For primitives, render inline
        return (
          <div key={key} className="a2ui__row">
            <RenderValue
              value={propValue}
              schema={propSchema}
              depth={depth + 1}
              label={formatLabel(key)}
            />
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatCellValue(
  value: unknown,
  schema?: Record<string, unknown>,
): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '✓' : '✗'
  if (typeof value === 'number') {
    const format = schema?.format as string | undefined
    if (format === 'currency' || format === 'money') {
      return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    }
    return value.toLocaleString()
  }
  return String(value)
}
