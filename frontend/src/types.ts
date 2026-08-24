export type HealthResponse = {
  status: string
  service: string
  version: string
}

export type ConnectionState =
  | { kind: 'checking'; message: string }
  | { kind: 'online'; message: string; data: HealthResponse }
  | { kind: 'offline'; message: string }

export type FileReference = {
  file: string
  language: string
  start_line: number
  end_line: number
  truncated: boolean
}

export type AnalysisStats = {
  received_files: number
  accepted_files: number
  skipped_files: number
  context_chars: number
  conversation_messages: number
}

export type AnalysisResponse = {
  answer: string
  references: FileReference[]
  mode: 'demo' | 'real'
  warnings: string[]
  stats: AnalysisStats
}

export type SearchMatch = {
  file: string
  language: string
  line_number: number
  column: number
  match_count: number
  line: string
  before: string[]
  after: string[]
  line_truncated: boolean
}

export type SearchResponse = {
  query: string
  results: SearchMatch[]
  warnings: string[]
  truncated: boolean
  stats: {
    received_files: number
    accepted_files: number
    skipped_files: number
    matched_files: number
    matched_lines: number
  }
}

export type CodeSymbol = {
  name: string
  kind:
    | 'function'
    | 'class'
    | 'interface'
    | 'type'
    | 'enum'
    | 'struct'
    | 'trait'
    | 'module'
  file: string
  language: string
  line_number: number
  declaration: string
}

export type SymbolResponse = {
  query: string | null
  symbols: CodeSymbol[]
  warnings: string[]
  truncated: boolean
  stats: {
    received_files: number
    accepted_files: number
    skipped_files: number
    symbol_files: number
    symbols: number
  }
}

export type IndexResponse = {
  status: 'completed' | 'partial'
  files: Array<{
    file: string
    language: string
    size_chars: number
    lines: number
    chunks: number
    symbols: number
  }>
  warnings: string[]
  stats: {
    received_files: number
    accepted_files: number
    skipped_files: number
    indexed_files: number
    chunks: number
    symbols: number
    content_chars: number
  }
}

export type AgentMode = 'plan' | 'execute'

export type AgentTaskStatus =
  | 'created'
  | 'planning'
  | 'waiting_for_confirmation'
  | 'queued'
  | 'executing'
  | 'reviewing'
  | 'validating'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'timed_out'
  | 'blocked'

export type ToolSpec = {
  name: string
  title: string
  description: string
  permission: 'read_only'
  requires_confirmation: boolean
  timeout_seconds: number
  max_output_chars: number
  parameters: Array<{
    name: string
    type: 'string'
    required: boolean
    description: string
    max_length: number | null
  }>
}

export type AgentTaskResponse = {
  task_id: string
  project_id: string | null
  goal: string
  mode: AgentMode
  status: AgentTaskStatus
  created_at: string
  updated_at: string
  file_count: number
  file_paths: string[]
  plan: Array<{
    id: string
    position: number
    title: string
    description: string
    status: 'pending' | 'in_progress' | 'completed' | 'skipped' | 'failed'
    tool_name: string | null
    arguments: Record<string, unknown>
    requires_confirmation: boolean
  }>
  tool_calls: Array<{
    id: string
    tool_name: string
    title: string
    status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'timed_out'
    arguments: Record<string, unknown>
    started_at: string | null
    finished_at: string | null
    duration_ms: number | null
    result: {
      summary: string
      item_count: number
      truncated: boolean
      evidence: Array<{
        file: string | null
        start_line: number | null
        end_line: number | null
        label: string
        preview: string | null
      }>
    } | null
    error: string | null
  }>
  transitions: Array<{
    from_status: AgentTaskStatus | null
    to_status: AgentTaskStatus
    at: string
    reason: string
  }>
  final_answer: string | null
  warnings: string[]
  queue: {
    status: 'queued' | 'running' | 'completed' | 'cancelled' | 'failed'
    attempts: number
    max_attempts: number
    available_at: string
    lease_expires_at: string | null
    heartbeat_at: string | null
    cancel_requested: boolean
    last_error: string | null
  } | null
  can_confirm: boolean
  can_cancel: boolean
  can_resume: boolean
}

export type AgentTaskListResponse = {
  tasks: AgentTaskResponse[]
  total: number
}

export type PatchStatus =
  | 'draft'
  | 'in_review'
  | 'approved'
  | 'rejected'
  | 'applied'
  | 'conflict'
  | 'reverted'

export type PatchResponse = {
  patch_id: string
  task_id: string
  status: PatchStatus
  summary: string
  risk: string
  created_at: string
  updated_at: string
  files: Array<{
    file: string
    language: string
    reason: string
    base_version: string
    proposed_version: string
    unified_diff: string
    additions: number
    deletions: number
    decision: 'pending' | 'accepted' | 'rejected'
  }>
  suggested_validators: string[]
  validations: Array<{
    validation_id: string
    patch_id: string
    status: 'passed' | 'failed' | 'timed_out' | 'skipped'
    created_at: string
    checks: Array<{
      validator: string
      title: string
      status: 'passed' | 'failed' | 'timed_out' | 'skipped'
      started_at: string
      finished_at: string
      duration_ms: number
      exit_code: number | null
      output: string
    }>
  }>
  events: Array<{
    action: string
    actor: 'local_user' | 'model' | 'system'
    at: string
    detail: string
  }>
  accepted_files: number
  rejected_files: number
  pending_files: number
  can_apply: boolean
  can_reject: boolean
  can_revert: boolean
  can_validate: boolean
  can_download: boolean
}

export type PatchListResponse = {
  patches: PatchResponse[]
  total: number
}

export type ValidatorSpec = {
  name: string
  title: string
  description: string
  executes_code: boolean
  execution_kind: 'builtin' | 'sandbox'
  available: boolean
  unavailable_reason: string | null
  timeout_seconds: number
  max_output_chars: number
}

export type UserResponse = {
  user_id: string
  email: string
  display_name: string
  is_system_admin: boolean
  created_at: string
}

export type AuthResponse = {
  access_token: string
  token_type: 'bearer'
  expires_at: string
  user: UserResponse
}

export type ProjectRole = 'owner' | 'admin' | 'editor' | 'viewer'
export type ProjectPermission = 'read' | 'write' | 'apply_patch' | 'manage' | 'delete'

export type OrganizationRole = 'owner' | 'admin' | 'member'

export type OrganizationResponse = {
  organization_id: string
  name: string
  description: string
  owner_user_id: string
  role: OrganizationRole
  created_at: string
  updated_at: string
}

export type OrganizationListResponse = {
  organizations: OrganizationResponse[]
  total: number
}

export type OrganizationMemberResponse = {
  user_id: string
  email: string
  display_name: string
  role: OrganizationRole
  created_at: string
}

export type OrganizationMemberListResponse = {
  members: OrganizationMemberResponse[]
  total: number
}

export type ProjectResponse = {
  project_id: string
  organization_id: string | null
  name: string
  description: string
  owner_user_id: string
  role: ProjectRole
  permissions: ProjectPermission[]
  run_mode: 'local' | 'service'
  default_model: string | null
  file_count: number
  index_status: string
  archived_at: string | null
  created_at: string
  updated_at: string
}

export type ProjectListResponse = {
  projects: ProjectResponse[]
  total: number
}

export type ProjectMemberResponse = {
  user_id: string
  email: string
  display_name: string
  role: ProjectRole
  created_at: string
}

export type ProjectMemberListResponse = {
  members: ProjectMemberResponse[]
  total: number
}

export type SandboxStatusResponse = {
  available: boolean
  runtime: string
  reason: string | null
  network: 'disabled'
  root_filesystem: 'read_only'
  workspace: 'temporary'
  allowed_commands: string[]
}

export type RetentionPolicyResponse = {
  expired_sessions_days: number
  login_attempts_days: number
  terminal_tasks_days: number
  patches_days: number
  validations_days: number
  audit_logs_days: number
  usage_records_days: number
  cleanup_interval_seconds: number
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  analysis?: AnalysisResponse
  task?: AgentTaskResponse
}
