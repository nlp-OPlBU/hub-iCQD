export type Status = "pending" | "running" | "completed" | "failed";

export interface SourceRef {
  url: string;
  title: string;
}

export interface Conclusion {
  text: string;
  sources: SourceRef[];
  is_model_inference: boolean;
}

export interface Section {
  heading: string;
  body: string;
  conclusions: Conclusion[];
}

export interface ReportContent {
  title: string;
  summary: string;
  sections: Section[];
  key_conclusions: Conclusion[];
  open_questions: string[];
}

export interface Source {
  url: string;
  title: string;
  site_name: string;
  snippet: string;
  accessed_at: string;
}

export interface ProcessStep {
  type: string; // plan | search | summarize | judge
  round: number;
  detail: Record<string, unknown>;
}

export interface ResearchProcess {
  plan: string[];
  search_queries: string[];
  reviewed_urls: string[];
  iterations: number;
  steps: ProcessStep[];
}

export interface DraftBlock {
  round: number;
  keyword: string;
  text: string;
}

export interface ConfidenceNote {
  overall: "high" | "medium" | "low";
  info_cutoff: string;
  notes: string[];
}

export interface ResearchRecord {
  research_id: string;
  topic: string;
  status: Status;
  created_at: string;
  updated_at: string;
  error: string | null;
  report: ReportContent | null;
  report_html: string;
  sources: Source[];
  draft: DraftBlock[];
  process: ResearchProcess | null;
  confidence: ConfidenceNote | null;
}
