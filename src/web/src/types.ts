export interface Artifact {
  id: string
  content_type: 'markdown' | 'image' | 'chart_data' | 'html' | 'checklist' | 'calendar' | 'a2ui'
  content: string
  title: string
  created_at: string
  component_name?: string
}

export interface ArtifactSummary {
  id: string
  title: string
  content_type: string
  created_at: string
  component_name?: string
}

/** A2UI component schema from the /api/components catalog. */
export interface ComponentSchema {
  name: string
  description: string
  skill: string
  props: Record<string, unknown>
}
