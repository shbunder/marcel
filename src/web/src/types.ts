export interface Artifact {
  id: string
  content_type: 'markdown' | 'image' | 'chart_data' | 'html' | 'checklist' | 'calendar'
  content: string
  title: string
  created_at: string
}

export interface ArtifactSummary {
  id: string
  title: string
  content_type: string
  created_at: string
}
