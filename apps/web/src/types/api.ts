export interface TaskConstraints {
  domains_allow?: string[];
  language?: string;
  max_rounds?: number;
}

export interface ResearchTaskProgress {
  current_state: string;
  events_total: number;
  latest_event_at: string;
  observability?: {
    search_result_count: number | null;
    selected_sources_from_search: Array<Record<string, any>>;
    selected_sources: Array<Record<string, any>>;
    fetch_succeeded: number | null;
    fetch_failed: number | null;
    attempted_sources: Array<Record<string, any>>;
    unattempted_sources: Array<Record<string, any>>;
    failed_sources: Array<Record<string, any>>;
    parse_decisions: Array<Record<string, any>>;
    source_quality_summary?: Record<string, any> | null;
    warnings: string[];
  } | null;
}

export interface ResearchTask {
  task_id: string;
  query: string;
  status: 'PLANNED' | 'PAUSED' | 'CANCELLED' | 'QUEUED' | 'RUNNING' | 'SEARCHING' | 'ACQUIRING' | 'PARSING' | 'INDEXING' | 'DRAFTING_CLAIMS' | 'VERIFYING' | 'REPORTING' | 'FAILED' | 'COMPLETED' | 'NEEDS_REVISION';
  constraints: TaskConstraints;
  revision_no: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  ended_at: string | null;
  progress?: ResearchTaskProgress;
}

export interface TaskEvent {
  event_id: string;
  run_id: string | null;
  event_type: string;
  sequence_no: number;
  payload: Record<string, any>;
  created_at: string;
}

export interface TaskEventListResponse {
  task_id: string;
  events: TaskEvent[];
}

export interface PipelineCounts {
  search_queries: number;
  candidate_urls: number;
  fetch_attempts: number;
  content_snapshots: number;
  source_documents: number;
  source_chunks: number;
  indexed_chunks: number;
  claims: number;
  claim_evidence: number;
  report_artifacts: number;
}

export interface PipelineFailure {
  failed_stage: string;
  reason: string;
  exception: string | null;
  message: string;
  next_action: string;
  counts: PipelineCounts;
  details?: Record<string, any> | null;
}

export interface PipelineRunResponse {
  task_id: string;
  status: ResearchTask['status'];
  completed: boolean;
  running_mode: string;
  stages_completed: string[];
  counts: PipelineCounts;
  report_artifact_id: string | null;
  report_version: number | null;
  report_markdown_preview: string | null;
  failure: PipelineFailure | null;
  dependencies: Record<string, any>;
}

export interface CandidateUrl {
  candidate_url_id: string;
  search_query_id: string;
  original_url: string;
  canonical_url: string;
  domain: string;
  title: string;
  rank: number;
  selected: boolean;
  metadata: Record<string, any>;
}

export interface SourceDocument {
  source_document_id: string;
  content_snapshot_id: string | null;
  canonical_url: string;
  domain: string;
  title: string | null;
  source_type: string;
  published_at: string | null;
  fetched_at: string;
}

export interface SourceDocumentListResponse {
  task_id: string;
  source_documents: SourceDocument[];
}

export interface SourceChunk {
  source_chunk_id: string;
  source_document_id: string;
  content_snapshot_id: string | null;
  chunk_no: number;
  token_count: number;
  text: string;
  metadata: Record<string, any>;
}

export interface SourceChunkListResponse {
  task_id: string;
  source_chunks: SourceChunk[];
}

export interface Claim {
  claim_id: string;
  statement: string;
  claim_type: string;
  confidence: number | null;
  verification_status: 'draft' | 'supported' | 'mixed' | 'unsupported';
  support_evidence_count: number;
  contradict_evidence_count: number;
  rationale: string | null;
  notes: Record<string, any>;
}

export interface ClaimListResponse {
  task_id: string;
  claims: Claim[];
}

export interface ClaimEvidence {
  claim_evidence_id: string;
  claim_id: string;
  citation_span_id: string;
  source_chunk_id: string;
  source_document_id: string;
  statement: string;
  relation_type: string;
  score: number | null;
  start_offset: number;
  end_offset: number;
  excerpt: string;
  normalized_excerpt_hash: string;
}

export interface ClaimEvidenceListResponse {
  task_id: string;
  claim_evidence: ClaimEvidence[];
}

export interface ReportArtifact {
  task_id: string;
  report_artifact_id: string;
  version: number;
  format: 'markdown';
  title: string;
  storage_bucket: string;
  storage_key: string;
  created_at: string;
  markdown: string;
}
