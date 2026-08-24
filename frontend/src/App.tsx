import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import {
  analyzeCode,
  applyPatch,
  buildProjectIndex,
  cancelAgentTask,
  confirmAgentTask,
  createAgentTask,
  downloadPatchFile,
  fetchAgentTask,
  fetchAgentTasks,
  fetchAgentTools,
  fetchCurrentUser,
  fetchHealth,
  fetchOrganizations,
  fetchProjects,
  fetchSandboxStatus,
  fetchSymbols,
  fetchTaskPatches,
  fetchValidators,
  generateTaskPatch,
  hasStoredAccessToken,
  rejectPatch,
  reviewPatchFile,
  resumeAgentTask,
  revertPatch,
  searchCode,
  setAccessToken,
  validatePatch,
} from './api'
import { AuthScreen, WorkspaceControls } from './Access'
import {
  formatFileSize,
  buildFileTree,
  getFileKey,
  getFileLanguage,
  getFilePath,
  selectCodeFiles,
  type FileRejection,
  type FileTreeNode,
} from './files'
import type {
  AgentTaskResponse,
  AgentTaskStatus,
  AuthResponse,
  ChatMessage,
  ConnectionState,
  IndexResponse,
  PatchResponse,
  PatchStatus,
  ProjectResponse,
  OrganizationResponse,
  SearchResponse,
  SandboxStatusResponse,
  SymbolResponse,
  UserResponse,
  ValidatorSpec,
} from './types'
import './App.css'

const starterPrompts = [
  { icon: '⌘', label: '解释项目结构' },
  { icon: '◇', label: '定位代码问题' },
  { icon: '✓', label: '检查修改方案' },
]

type IndexUiState =
  | { kind: 'idle' }
  | { kind: 'indexing' }
  | { kind: 'completed' | 'partial'; data: IndexResponse }
  | { kind: 'failed'; message: string }

const taskStatusLabels: Record<AgentTaskStatus, string> = {
  created: '已创建',
  planning: '规划中',
  waiting_for_confirmation: '等待确认',
  queued: '排队中',
  executing: '执行中',
  reviewing: '审阅中',
  validating: '校验中',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失败',
  timed_out: '超时',
  blocked: '已阻止',
}

const planStatusLabels = {
  pending: '待执行',
  in_progress: '进行中',
  completed: '已完成',
  skipped: '已跳过',
  failed: '失败',
} as const

const patchStatusLabels: Record<PatchStatus, string> = {
  draft: '草稿',
  in_review: '审阅中',
  approved: '已批准',
  rejected: '已拒绝',
  applied: '已应用到内存',
  conflict: '版本冲突',
  reverted: '已撤销',
}

function App() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const directoryInputRef = useRef<HTMLInputElement>(null)
  const analysisAbortRef = useRef<AbortController | null>(null)
  const taskAbortRef = useRef<AbortController | null>(null)
  const patchAbortRef = useRef<AbortController | null>(null)
  const searchAbortRef = useRef<AbortController | null>(null)
  const symbolAbortRef = useRef<AbortController | null>(null)
  const indexAbortRef = useRef<AbortController | null>(null)
  const chatThreadRef = useRef<HTMLElement>(null)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [fileNotice, setFileNotice] = useState<string | null>(null)
  const [fileRejections, setFileRejections] = useState<FileRejection[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResponse, setSearchResponse] = useState<SearchResponse | null>(null)
  const [searchPending, setSearchPending] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [symbolQuery, setSymbolQuery] = useState('')
  const [symbolResponse, setSymbolResponse] = useState<SymbolResponse | null>(null)
  const [symbolPending, setSymbolPending] = useState(false)
  const [symbolError, setSymbolError] = useState<string | null>(null)
  const [indexState, setIndexState] = useState<IndexUiState>({ kind: 'idle' })
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [analysisPending, setAnalysisPending] = useState(false)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [interactionMode, setInteractionMode] = useState<'agent' | 'analysis'>('agent')
  const [taskPending, setTaskPending] = useState(false)
  const [recentAgentTasks, setRecentAgentTasks] = useState<AgentTaskResponse[]>([])
  const [registeredToolCount, setRegisteredToolCount] = useState(0)
  const [registeredValidatorCount, setRegisteredValidatorCount] = useState(0)
  const [validators, setValidators] = useState<ValidatorSpec[]>([])
  const [sandboxStatus, setSandboxStatus] = useState<SandboxStatusResponse | null>(null)
  const [sandboxValidator, setSandboxValidator] = useState('sandbox_pytest')
  const [taskPatches, setTaskPatches] = useState<Record<string, PatchResponse[]>>({})
  const [patchPending, setPatchPending] = useState(false)
  const [patchNotice, setPatchNotice] = useState<string | null>(null)
  const [patchNoticeTaskId, setPatchNoticeTaskId] = useState<string | null>(null)
  const [authState, setAuthState] = useState<'restoring' | 'signed_out' | 'signed_in'>(() =>
    hasStoredAccessToken() ? 'restoring' : 'signed_out',
  )
  const [currentUser, setCurrentUser] = useState<UserResponse | null>(null)
  const [projects, setProjects] = useState<ProjectResponse[]>([])
  const [organizations, setOrganizations] = useState<OrganizationResponse[]>([])
  const [activeOrganizationId, setActiveOrganizationId] = useState<string | null>(null)
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)
  const [connection, setConnection] = useState<ConnectionState>({
    kind: 'checking',
    message: '正在连接本地后端…',
  })

  const checkConnection = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await fetchHealth(signal)
      setConnection({
        kind: 'online',
        message: `${data.service} · v${data.version}`,
        data,
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }

      setConnection({
        kind: 'offline',
        message: '无法连接后端，请确认 FastAPI 已在 8000 端口启动。',
      })
    }
  }, [])

  const retryConnection = useCallback(() => {
    setConnection({ kind: 'checking', message: '正在连接本地后端…' })
    void checkConnection()
  }, [checkConnection])

  const loadWorkspace = useCallback(async (user: UserResponse, signal?: AbortSignal) => {
    const [organizationList, projectList] = await Promise.all([
      fetchOrganizations(signal),
      fetchProjects(signal),
    ])
    const storedOrganizationId = window.sessionStorage.getItem('codexxx_active_organization')
    const activeOrganization =
      organizationList.organizations.find((organization) => organization.organization_id === storedOrganizationId) ??
      organizationList.organizations[0] ?? null
    const organizationProjects = activeOrganization
      ? projectList.projects.filter((project) => project.organization_id === activeOrganization.organization_id)
      : projectList.projects
    const storedProjectId = window.sessionStorage.getItem('codexxx_active_project')
    const activeProject =
      organizationProjects.find((project) => project.project_id === storedProjectId) ??
      organizationProjects.find((project) => !project.archived_at) ??
      organizationProjects[0] ??
      null
    const taskList = activeProject
      ? await fetchAgentTasks(activeProject.project_id, signal)
      : { tasks: [], total: 0 }
    setCurrentUser(user)
    setOrganizations(organizationList.organizations)
    setActiveOrganizationId(activeOrganization?.organization_id ?? null)
    setProjects(organizationProjects)
    setActiveProjectId(activeProject?.project_id ?? null)
    setRecentAgentTasks(taskList.tasks)
    if (activeProject) {
      window.sessionStorage.setItem('codexxx_active_project', activeProject.project_id)
    } else {
      window.sessionStorage.removeItem('codexxx_active_project')
    }
    if (activeOrganization) {
      window.sessionStorage.setItem('codexxx_active_organization', activeOrganization.organization_id)
    } else {
      window.sessionStorage.removeItem('codexxx_active_organization')
    }
    setAuthState('signed_in')
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    Promise.all([
      fetchHealth(controller.signal),
      fetchAgentTools(controller.signal),
      fetchValidators(controller.signal),
      fetchSandboxStatus(controller.signal),
    ])
      .then(([data, tools, availableValidators, sandbox]) => {
        setConnection({
          kind: 'online',
          message: `${data.service} · v${data.version}`,
          data,
        })
        setRegisteredToolCount(tools.length)
        setRegisteredValidatorCount(availableValidators.length)
        setValidators(availableValidators)
        setSandboxStatus(sandbox)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }

        setConnection({
          kind: 'offline',
          message: '无法连接后端，请确认 FastAPI 已在 8000 端口启动。',
        })
      })

    if (hasStoredAccessToken()) {
      void fetchCurrentUser(controller.signal)
        .then((user) => loadWorkspace(user, controller.signal))
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === 'AbortError') {
            return
          }
          setAccessToken(null)
          setAuthState('signed_out')
        })
    }

    return () => {
      controller.abort()
      analysisAbortRef.current?.abort()
      taskAbortRef.current?.abort()
      patchAbortRef.current?.abort()
      searchAbortRef.current?.abort()
      symbolAbortRef.current?.abort()
      indexAbortRef.current?.abort()
    }
  }, [loadWorkspace])

  useEffect(() => {
    const thread = chatThreadRef.current
    if (thread) {
      thread.scrollTo({ top: thread.scrollHeight, behavior: 'smooth' })
    }
  }, [messages, analysisPending, taskPending])

  const busy = analysisPending || taskPending || patchPending
  const activeProject = projects.find(
    (project) => project.project_id === activeProjectId,
  ) ?? null
  const builtinValidatorNames = validators
    .filter((validator) => validator.execution_kind === 'builtin')
    .map((validator) => validator.name)
  const sandboxValidators = validators.filter(
    (validator) => validator.execution_kind === 'sandbox',
  )

  const openFilePicker = () => {
    if (busy) {
      return
    }
    fileInputRef.current?.click()
  }

  const openDirectoryPicker = () => {
    if (busy) {
      return
    }
    directoryInputRef.current?.click()
  }

  const resetProjectTools = () => {
    searchAbortRef.current?.abort()
    symbolAbortRef.current?.abort()
    indexAbortRef.current?.abort()
    searchAbortRef.current = null
    symbolAbortRef.current = null
    indexAbortRef.current = null
    setSearchPending(false)
    setSymbolPending(false)
    setSearchResponse(null)
    setSearchError(null)
    setSymbolResponse(null)
    setSymbolError(null)
    setIndexState({ kind: 'idle' })
  }

  const handleFileSelection = (
    event: ChangeEvent<HTMLInputElement>,
    source: 'files' | 'directory' = 'files',
  ) => {
    const incomingFiles = Array.from(event.target.files ?? [])

    if (incomingFiles.length === 0) {
      return
    }

    const result = selectCodeFiles(selectedFiles, incomingFiles)
    const noticeParts: string[] = []

    if (result.addedCount > 0) {
      noticeParts.push(
        source === 'directory'
          ? `已从项目目录载入 ${result.addedCount} 个文件`
          : `已添加 ${result.addedCount} 个文件`,
      )
    }

    if (result.duplicateCount > 0) {
      noticeParts.push(`跳过 ${result.duplicateCount} 个重复文件`)
    }

    if (result.rejections.length > 0) {
      noticeParts.push(`${result.rejections.length} 个文件未载入`)
    }

    setSelectedFiles(result.files)
    resetProjectTools()
    setFileNotice(noticeParts.join('，'))
    setFileRejections(result.rejections)
    event.target.value = ''
  }

  const removeFile = (fileToRemove: File) => {
    if (busy) {
      return
    }
    const fileKey = getFileKey(fileToRemove)
    setSelectedFiles((files) => files.filter((file) => getFileKey(file) !== fileKey))
    resetProjectTools()
    setFileNotice(`已移除 ${getFilePath(fileToRemove)}`)
    setFileRejections([])
  }

  const clearFiles = () => {
    if (busy) {
      return
    }
    setSelectedFiles([])
    setSearchQuery('')
    setSymbolQuery('')
    resetProjectTools()
    setFileNotice('已清空代码文件')
    setFileRejections([])
  }

  const totalFileSize = selectedFiles.reduce((sum, file) => sum + file.size, 0)
  const fileTree = useMemo(() => buildFileTree(selectedFiles), [selectedFiles])

  const renderFileTreeNodes = (nodes: FileTreeNode[], depth = 0) =>
    nodes.map((node) => {
      if (node.kind === 'directory') {
        return (
          <details
            className="file-tree-directory"
            key={node.path}
            open={depth < 2}
          >
            <summary>
              <span aria-hidden="true">▸</span>
              <strong>{node.name}</strong>
              <small>{node.children.length}</small>
            </summary>
            <div className="file-tree-children">
              {renderFileTreeNodes(node.children, depth + 1)}
            </div>
          </details>
        )
      }

      return (
        <div className="file-tree-file" key={node.path} title={node.path}>
          <span aria-hidden="true">{'</>'}</span>
          <strong>{node.name}</strong>
          <small>{getFileLanguage(node.path)}</small>
          <button
            type="button"
            onClick={() => removeFile(node.file)}
            disabled={busy}
            aria-label={`移除 ${node.path}`}
          >
            ×
          </button>
        </div>
      )
    })

  const submitSearch = async () => {
    const cleanedQuery = searchQuery.trim()
    if (!cleanedQuery) {
      setSearchError('请输入要搜索的文本关键词。')
      return
    }
    if (selectedFiles.length === 0) {
      setSearchError('请先选择代码文件或项目目录。')
      return
    }
    if (connection.kind !== 'online') {
      setSearchError('后端尚未连接，暂时无法搜索。')
      return
    }
    if (searchPending) {
      return
    }

    const controller = new AbortController()
    searchAbortRef.current?.abort()
    searchAbortRef.current = controller
    setSearchPending(true)
    setSearchError(null)

    try {
      setSearchResponse(
        await searchCode(cleanedQuery, selectedFiles, controller.signal),
      )
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      setSearchError(error instanceof Error ? error.message : '搜索失败，请稍后重试。')
    } finally {
      if (searchAbortRef.current === controller) {
        searchAbortRef.current = null
        setSearchPending(false)
      }
    }
  }

  const submitSymbols = async () => {
    if (selectedFiles.length === 0) {
      setSymbolError('请先选择代码文件或项目目录。')
      return
    }
    if (connection.kind !== 'online') {
      setSymbolError('后端尚未连接，暂时无法检索符号。')
      return
    }
    if (symbolPending) {
      return
    }

    const controller = new AbortController()
    symbolAbortRef.current?.abort()
    symbolAbortRef.current = controller
    setSymbolPending(true)
    setSymbolError(null)

    try {
      setSymbolResponse(
        await fetchSymbols(
          selectedFiles,
          symbolQuery.trim() || undefined,
          controller.signal,
        ),
      )
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      setSymbolError(
        error instanceof Error ? error.message : '符号检索失败，请稍后重试。',
      )
    } finally {
      if (symbolAbortRef.current === controller) {
        symbolAbortRef.current = null
        setSymbolPending(false)
      }
    }
  }

  const runIndexing = async () => {
    if (selectedFiles.length === 0) {
      setIndexState({ kind: 'failed', message: '请先选择代码文件或项目目录。' })
      return
    }
    if (connection.kind !== 'online') {
      setIndexState({ kind: 'failed', message: '后端尚未连接，暂时无法建立索引。' })
      return
    }
    if (indexState.kind === 'indexing') {
      return
    }

    const controller = new AbortController()
    indexAbortRef.current?.abort()
    indexAbortRef.current = controller
    setIndexState({ kind: 'indexing' })

    try {
      const result = await buildProjectIndex(selectedFiles, controller.signal)
      setIndexState({ kind: result.status, data: result })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      setIndexState({
        kind: 'failed',
        message: error instanceof Error ? error.message : '项目索引失败，请稍后重试。',
      })
    } finally {
      if (indexAbortRef.current === controller) {
        indexAbortRef.current = null
      }
    }
  }

  const submitQuestion = async () => {
    const cleanedQuestion = question.trim()

    if (!cleanedQuestion) {
      setAnalysisError('请输入要分析的代码问题。')
      return
    }

    if (selectedFiles.length === 0) {
      setAnalysisError('请先添加至少一个代码文件，再提交问题。')
      return
    }

    if (connection.kind !== 'online') {
      setAnalysisError('后端尚未连接，请启动 FastAPI 服务后重试。')
      return
    }

    if (busy) {
      return
    }

    const conversation = messages.map(({ role, content }) => ({ role, content }))
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: cleanedQuestion,
    }
    const controller = new AbortController()

    analysisAbortRef.current?.abort()
    analysisAbortRef.current = controller
    setAnalysisError(null)
    setAnalysisPending(true)
    setMessages((currentMessages) => [...currentMessages, userMessage])
    setQuestion('')

    try {
      const result = await analyzeCode(
        cleanedQuestion,
        selectedFiles,
        conversation,
        controller.signal,
      )
      setMessages((currentMessages) => [
        ...currentMessages,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: result.answer,
          analysis: result,
        },
      ])
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }

      setAnalysisError(
        error instanceof Error
          ? error.message
          : '分析请求失败，请稍后重试。',
      )
      setMessages((currentMessages) =>
        currentMessages.filter((message) => message.id !== userMessage.id),
      )
      setQuestion(cleanedQuestion)
    } finally {
      if (analysisAbortRef.current === controller) {
        analysisAbortRef.current = null
        setAnalysisPending(false)
      }
    }
  }

  const updateTaskSnapshot = (task: AgentTaskResponse) => {
    setMessages((currentMessages) => {
      const taskMessageIndex = currentMessages.findIndex(
        (message) => message.task?.task_id === task.task_id,
      )
      if (taskMessageIndex === -1) {
        return [
          ...currentMessages,
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: task.final_answer ?? 'Agent 计划已生成，等待你确认执行只读工具。',
            task,
          },
        ]
      }
      return currentMessages.map((message, index) =>
        index === taskMessageIndex
          ? {
              ...message,
              content:
                task.final_answer ??
                (task.status === 'waiting_for_confirmation'
                  ? 'Agent 计划已生成，等待你确认执行只读工具。'
                  : `Agent 任务状态：${taskStatusLabels[task.status]}`),
              task,
            }
          : message,
      )
    })
    setRecentAgentTasks((tasks) => [
      task,
      ...tasks.filter((item) => item.task_id !== task.task_id),
    ].slice(0, 20))
  }

  const updatePatchSnapshot = (patch: PatchResponse) => {
    setTaskPatches((current) => {
      const existing = current[patch.task_id] ?? []
      const existingIndex = existing.findIndex((item) => item.patch_id === patch.patch_id)
      const next = [...existing]
      if (existingIndex >= 0) {
        next[existingIndex] = patch
      } else {
        // Keep the original patch at the top; a regenerated patch is appended
        // so it does not visually replace the history above it.
        next.push(patch)
      }
      return { ...current, [patch.task_id]: next }
    })
  }

  const loadTaskPatches = async (taskId: string, signal?: AbortSignal) => {
    try {
      const result = await fetchTaskPatches(taskId, signal)
      setTaskPatches((current) => ({ ...current, [taskId]: result.patches }))
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      setAnalysisError(
        error instanceof Error ? error.message : '补丁列表加载失败，请稍后重试。',
      )
    }
  }

  const createTask = async () => {
    const cleanedQuestion = question.trim()
    if (!cleanedQuestion) {
      setAnalysisError('请输入要交给 Agent 处理的代码任务。')
      return
    }
    if (selectedFiles.length === 0) {
      setAnalysisError('请先添加至少一个代码文件，再创建 Agent 任务。')
      return
    }
    if (!activeProjectId) {
      setAnalysisError('请先创建或选择一个项目。')
      return
    }
    if (connection.kind !== 'online') {
      setAnalysisError('后端尚未连接，请启动 FastAPI 服务后重试。')
      return
    }
    if (busy) {
      return
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: cleanedQuestion,
    }
    const controller = new AbortController()
    taskAbortRef.current?.abort()
    taskAbortRef.current = controller
    setTaskPending(true)
    setAnalysisError(null)
    setMessages((currentMessages) => [...currentMessages, userMessage])
    setQuestion('')

    try {
      updateTaskSnapshot(
        await createAgentTask(
          cleanedQuestion,
          selectedFiles,
          activeProjectId,
          controller.signal,
        ),
      )
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      setAnalysisError(
        error instanceof Error ? error.message : 'Agent 任务创建失败，请稍后重试。',
      )
      setMessages((currentMessages) =>
        currentMessages.filter((message) => message.id !== userMessage.id),
      )
      setQuestion(cleanedQuestion)
    } finally {
      if (taskAbortRef.current === controller) {
        taskAbortRef.current = null
        setTaskPending(false)
      }
    }
  }

  const runTaskAction = async (
    task: AgentTaskResponse,
    action: 'confirm' | 'cancel' | 'resume',
  ) => {
    if (busy && action !== 'cancel') {
      return
    }
    const controller = new AbortController()
    taskAbortRef.current?.abort()
    taskAbortRef.current = controller
    setTaskPending(true)
    setAnalysisError(null)

    try {
      const updatedTask =
        action === 'confirm'
          ? await confirmAgentTask(task.task_id, controller.signal)
          : action === 'cancel'
            ? await cancelAgentTask(task.task_id, controller.signal)
            : await resumeAgentTask(task.task_id, controller.signal)
      updateTaskSnapshot(updatedTask)
      if (action === 'confirm' && ['queued', 'executing'].includes(updatedTask.status)) {
        let currentTask = updatedTask
        while (
          ['queued', 'executing', 'reviewing', 'validating'].includes(currentTask.status) &&
          !controller.signal.aborted
        ) {
          await new Promise((resolve) => window.setTimeout(resolve, 250))
          currentTask = await fetchAgentTask(task.task_id, controller.signal)
          updateTaskSnapshot(currentTask)
        }
        if (currentTask.status === 'completed') {
          await loadTaskPatches(currentTask.task_id, controller.signal)
        }
      }
      if (action === 'cancel' && ['queued', 'executing'].includes(updatedTask.status)) {
        let currentTask = updatedTask
        while (
          ['queued', 'executing', 'reviewing', 'validating'].includes(currentTask.status) &&
          !controller.signal.aborted
        ) {
          await new Promise((resolve) => window.setTimeout(resolve, 100))
          currentTask = await fetchAgentTask(task.task_id, controller.signal)
          updateTaskSnapshot(currentTask)
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      setAnalysisError(
        error instanceof Error ? error.message : 'Agent 任务操作失败，请稍后重试。',
      )
    } finally {
      if (taskAbortRef.current === controller) {
        taskAbortRef.current = null
        setTaskPending(false)
      }
    }
  }

  const runPatchOperation = async (
    operation: () => Promise<PatchResponse>,
    fallbackMessage: string,
    successMessage?: string,
  ): Promise<boolean> => {
    if (busy) {
      return false
    }
    const controller = new AbortController()
    patchAbortRef.current?.abort()
    patchAbortRef.current = controller
    setPatchPending(true)
    setAnalysisError(null)
    setPatchNotice(null)
    try {
      updatePatchSnapshot(await operation())
      if (successMessage) {
        setPatchNotice(successMessage)
      }
      return true
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return false
      }
      setAnalysisError(error instanceof Error ? error.message : fallbackMessage)
      return false
    } finally {
      if (patchAbortRef.current === controller) {
        patchAbortRef.current = null
        setPatchPending(false)
      }
    }
  }

  const generatePatch = async (task: AgentTaskResponse) => {
    const succeeded = await runPatchOperation(
      () => generateTaskPatch(task.task_id, patchAbortRef.current?.signal),
      '补丁草稿生成失败，请稍后重试。',
      '补丁已生成',
    )
    if (succeeded) {
      setPatchNoticeTaskId(task.task_id)
    }
  }

  const reviewPatch = async (
    patch: PatchResponse,
    file: string,
    decision: 'accepted' | 'rejected',
  ) => {
    await runPatchOperation(
      () => reviewPatchFile(patch.patch_id, file, decision, patchAbortRef.current?.signal),
      '补丁文件审阅失败，请稍后重试。',
    )
  }

  const runPatchAction = async (
    patch: PatchResponse,
    action: 'reject' | 'apply' | 'revert' | 'validate',
  ) => {
    if (
      action === 'apply' &&
      !window.confirm(
        '确认把已接受的修改应用到后端内存快照？这不会覆盖你在浏览器中选择的本地原文件。',
      )
    ) {
      return
    }
    if (
      action === 'revert' &&
      !window.confirm('确认撤销该补丁并恢复后端内存快照？')
    ) {
      return
    }
    await runPatchOperation(
      () =>
        action === 'reject'
          ? rejectPatch(patch.patch_id, patchAbortRef.current?.signal)
          : action === 'apply'
            ? applyPatch(patch.patch_id, patchAbortRef.current?.signal)
            : action === 'revert'
              ? revertPatch(patch.patch_id, patchAbortRef.current?.signal)
              : validatePatch(
                  patch.patch_id,
                  builtinValidatorNames,
                  false,
                  patchAbortRef.current?.signal,
                ),
      '补丁操作失败，请稍后重试。',
    )
  }

  const runSandboxValidation = async (patch: PatchResponse) => {
    const selected = sandboxValidators.find(
      (validator) => validator.name === sandboxValidator,
    )
    if (!selected?.available || !sandboxStatus?.available) {
      setAnalysisError(selected?.unavailable_reason ?? sandboxStatus?.reason ?? '容器沙箱不可用。')
      return
    }
    if (!window.confirm(`确认在隔离沙箱中运行“${selected.title}”？`)) {
      return
    }
    await runPatchOperation(
      () => validatePatch(
        patch.patch_id,
        [selected.name],
        true,
        patchAbortRef.current?.signal,
      ),
      '沙箱验证失败，请稍后重试。',
    )
  }

  const downloadPatchedFile = async (patch: PatchResponse, file: string) => {
    if (busy) {
      return
    }
    const controller = new AbortController()
    patchAbortRef.current?.abort()
    patchAbortRef.current = controller
    setPatchPending(true)
    setAnalysisError(null)
    try {
      const blob = await downloadPatchFile(patch.patch_id, file, controller.signal)
      const downloadUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = downloadUrl
      anchor.download = file.split('/').pop() ?? 'patched-file.txt'
      anchor.click()
      URL.revokeObjectURL(downloadUrl)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      setAnalysisError(
        error instanceof Error ? error.message : '更新后文件下载失败，请稍后重试。',
      )
    } finally {
      if (patchAbortRef.current === controller) {
        patchAbortRef.current = null
        setPatchPending(false)
      }
    }
  }

  const handleQuestionKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) {
      return
    }

    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void (interactionMode === 'agent' ? createTask() : submitQuestion())
    }
  }

  const handleComposerSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void (interactionMode === 'agent' ? createTask() : submitQuestion())
  }

  const applyStarterPrompt = (prompt: string) => {
    setQuestion(prompt)
    setAnalysisError(null)
  }

  const handleAuthenticated = async (auth: AuthResponse) => {
    await loadWorkspace(auth.user)
  }

  const clearWorkspaceSession = () => {
    taskAbortRef.current?.abort()
    patchAbortRef.current?.abort()
    setCurrentUser(null)
    setProjects([])
    setOrganizations([])
    setActiveOrganizationId(null)
    setActiveProjectId(null)
    setRecentAgentTasks([])
    setTaskPatches({})
    setPatchNotice(null)
    setPatchNoticeTaskId(null)
    setMessages([])
    setSelectedFiles([])
    window.sessionStorage.removeItem('codexxx_active_project')
    window.sessionStorage.removeItem('codexxx_active_organization')
    setAuthState('signed_out')
  }

  const switchOrganization = (organizationId: string) => {
    if (!organizationId || organizationId === activeOrganizationId) {
      return
    }
    setActiveOrganizationId(organizationId)
    window.sessionStorage.setItem('codexxx_active_organization', organizationId)
    void fetchProjects()
      .then((projectList) => {
        const nextProjects = projectList.projects.filter((project) => project.organization_id === organizationId)
        const nextProject = nextProjects.find((project) => !project.archived_at) ?? nextProjects[0] ?? null
        setProjects(nextProjects)
        setActiveProjectId(nextProject?.project_id ?? null)
        setRecentAgentTasks([])
        setMessages([])
        setTaskPatches({})
        setAnalysisError(null)
        if (nextProject) {
          window.sessionStorage.setItem('codexxx_active_project', nextProject.project_id)
          return fetchAgentTasks(nextProject.project_id)
        }
        window.sessionStorage.removeItem('codexxx_active_project')
        return null
      })
      .then((taskList) => {
        if (taskList) {
          setRecentAgentTasks(taskList.tasks)
        }
      })
      .catch((error: unknown) => {
        setAnalysisError(error instanceof Error ? error.message : '组织项目加载失败。')
      })
  }

  const saveOrganizationSnapshot = (organization: OrganizationResponse, makeActive: boolean) => {
    setOrganizations((current) => [
      organization,
      ...current.filter((item) => item.organization_id !== organization.organization_id),
    ])
    if (makeActive) {
      switchOrganization(organization.organization_id)
    }
  }

  const switchProject = (projectId: string) => {
    if (!projectId || projectId === activeProjectId) {
      return
    }
    setActiveProjectId(projectId)
    window.sessionStorage.setItem('codexxx_active_project', projectId)
    setRecentAgentTasks([])
    setMessages([])
    setTaskPatches({})
    setAnalysisError(null)
    void fetchAgentTasks(projectId)
      .then((taskList) => setRecentAgentTasks(taskList.tasks))
      .catch((error: unknown) => {
        setAnalysisError(error instanceof Error ? error.message : '项目任务加载失败。')
      })
  }

  const saveProjectSnapshot = (project: ProjectResponse, makeActive: boolean) => {
    setProjects((current) => [
      project,
      ...current.filter((item) => item.project_id !== project.project_id),
    ])
    if (makeActive || !activeProjectId) {
      setActiveProjectId(project.project_id)
      window.sessionStorage.setItem('codexxx_active_project', project.project_id)
      setRecentAgentTasks([])
      setMessages([])
      setTaskPatches({})
    }
  }

  const removeProjectSnapshot = (projectId: string) => {
    const remaining = projects.filter((project) => project.project_id !== projectId)
    setProjects(remaining)
    if (activeProjectId === projectId) {
      const nextProject = remaining.find((project) => !project.archived_at) ?? remaining[0]
      setActiveProjectId(nextProject?.project_id ?? null)
      setRecentAgentTasks([])
      setMessages([])
      setTaskPatches({})
      if (nextProject) {
        window.sessionStorage.setItem('codexxx_active_project', nextProject.project_id)
        void fetchAgentTasks(nextProject.project_id)
          .then((taskList) => setRecentAgentTasks(taskList.tasks))
          .catch((error: unknown) => {
            setAnalysisError(error instanceof Error ? error.message : '项目任务加载失败。')
          })
      } else {
        window.sessionStorage.removeItem('codexxx_active_project')
      }
    }
  }

  if (authState === 'restoring') {
    return (
      <main className="auth-shell">
        <div className="auth-restoring" role="status">
          <span className="agent-logo" aria-hidden="true"><i /></span>
          正在恢复工作区…
        </div>
      </main>
    )
  }

  if (authState === 'signed_out' || !currentUser) {
    return (
      <AuthScreen
        backendOnline={connection.kind === 'online'}
        onAuthenticated={handleAuthenticated}
      />
    )
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              C<span>X</span>
            </span>
            <span className="brand-name">CodeXXX</span>
          </div>
          <button
            className="sidebar-action"
            type="button"
            disabled={busy}
            aria-label="新建任务"
            title="新建任务"
            onClick={() => {
              setMessages([])
              setQuestion('')
              setAnalysisError(null)
            }}
          >
            ＋
          </button>
        </div>

        <button
          className="new-task-button"
          type="button"
          disabled={busy}
          onClick={() => {
            setMessages([])
            setQuestion('')
            setAnalysisError(null)
          }}
        >
          <span aria-hidden="true">＋</span>
          新建任务
        </button>

        <nav className="task-list" aria-label="最近任务">
          <p className="sidebar-label">任务</p>
          {recentAgentTasks.length > 0 ? (
            recentAgentTasks.slice(0, 8).map((task, index) => (
              <button
                className={`task-item ${index === 0 ? 'is-active' : ''}`}
                key={task.task_id}
                type="button"
                onClick={() => {
                  const message = messages.find(
                    (item) => item.task?.task_id === task.task_id,
                  )
                  if (message) {
                    document.getElementById(`message-${message.id}`)?.scrollIntoView({
                      behavior: 'smooth',
                      block: 'center',
                    })
                  } else {
                    updateTaskSnapshot(task)
                  }
                }}
              >
                <span className="task-icon" aria-hidden="true">
                  {task.status === 'completed' ? '✓' : task.status === 'cancelled' ? '×' : '◉'}
                </span>
                <span>
                  <strong title={task.goal}>{task.goal}</strong>
                  <small>{taskStatusLabels[task.status]}</small>
                </span>
              </button>
            ))
          ) : (
            <div className="task-list-empty">
              尚无 Agent 任务
              <small>{registeredToolCount} 个只读工具已注册</small>
            </div>
          )}
        </nav>

        <WorkspaceControls
          key={`${activeOrganizationId ?? 'no-organization'}:${activeProjectId ?? 'no-project'}`}
          user={currentUser}
          organizations={organizations}
          activeOrganizationId={activeOrganizationId}
          onActiveOrganizationChange={switchOrganization}
          projects={projects}
          activeProjectId={activeProjectId}
          onActiveProjectChange={switchProject}
          onProjectSaved={saveProjectSnapshot}
          onOrganizationSaved={saveOrganizationSnapshot}
          onProjectDeleted={removeProjectSnapshot}
          onSessionEnded={clearWorkspaceSession}
        />
      </aside>

      <main className="chat-workspace">
        <header className="chat-header">
          <div className="chat-title">
            <h1>CodeXXX</h1>
            <span>{activeProject?.name ?? '请选择项目'}</span>
          </div>

          <div className={`connection-status is-${connection.kind}`}>
            <span className="connection-dot" />
            <span>
              {connection.kind === 'online'
                ? '已连接'
                : connection.kind === 'checking'
                  ? '连接中'
                  : '未连接'}
            </span>
            {connection.kind === 'offline' && (
              <button type="button" onClick={retryConnection}>
                重试
              </button>
            )}
          </div>
        </header>

        <section ref={chatThreadRef} className="chat-thread" aria-label="CodeXXX 对话">
          {messages.length === 0 && !busy ? (
            <div className="empty-chat">
              <span className="agent-logo" aria-hidden="true">
                <i />
              </span>
              <h2>我能帮你处理什么代码任务？</h2>
              <p>
                选择代码文件或项目上下文，然后描述你想理解、排查或修改的内容。
              </p>
              <div className="starter-prompts" aria-label="示例任务">
                {starterPrompts.map((prompt) => (
                  <button
                    type="button"
                    key={prompt.label}
                    onClick={() => applyStarterPrompt(prompt.label)}
                  >
                    <span aria-hidden="true">{prompt.icon}</span>
                    {prompt.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <article
                  className={`message message-${message.role}`}
                  key={message.id}
                  id={`message-${message.id}`}
                >
                  <div className="message-avatar" aria-hidden="true">
                    {message.role === 'user' ? '你' : 'CX'}
                  </div>
                  <div className="message-body">
                    <div className="message-heading">
                      <strong>{message.role === 'user' ? '你' : 'CodeXXX'}</strong>
                      {message.analysis && (
                        <span className={`mode-badge mode-${message.analysis.mode}`}>
                          {message.analysis.mode === 'demo' ? '演示模式' : '真实模型'}
                        </span>
                      )}
                      {message.task && (
                        <span className={`task-status-badge is-${message.task.status}`}>
                          {taskStatusLabels[message.task.status]}
                        </span>
                      )}
                    </div>
                    <p className="message-content">{message.content}</p>

                    {message.analysis && message.analysis.references.length > 0 && (
                      <section className="response-section">
                        <h3>引用文件</h3>
                        <div className="reference-list">
                          {message.analysis.references.map((reference) => (
                            <div
                              className="reference-item"
                              key={`${message.id}:${reference.file}:${reference.start_line}`}
                            >
                              <span className="reference-icon" aria-hidden="true">
                                {'</>'}
                              </span>
                              <span>
                                <strong>{reference.file}</strong>
                                <small>
                                  {reference.language} · 第 {reference.start_line}–
                                  {reference.end_line} 行
                                  {reference.truncated ? ' · 已截断' : ''}
                                </small>
                              </span>
                            </div>
                          ))}
                        </div>
                      </section>
                    )}

                    {message.analysis && message.analysis.warnings.length > 0 && (
                      <details className="warning-panel">
                        <summary>
                          {message.analysis.warnings.length} 条处理提示
                        </summary>
                        <ul>
                          {message.analysis.warnings.map((warning, index) => (
                            <li key={`${message.id}:warning:${index}`}>{warning}</li>
                          ))}
                        </ul>
                      </details>
                    )}

                    {message.analysis && (
                      <div className="response-stats">
                        <span>接收 {message.analysis.stats.received_files} 个文件</span>
                        <span>使用 {message.analysis.stats.accepted_files} 个</span>
                        {message.analysis.stats.skipped_files > 0 && (
                          <span>跳过 {message.analysis.stats.skipped_files} 个</span>
                        )}
                        <span>
                          上下文 {message.analysis.stats.context_chars.toLocaleString()} 字符
                        </span>
                      </div>
                    )}

                    {message.task && (
                      <section className="agent-task-panel">
                        <div className="agent-task-meta">
                          <span>{message.task.mode === 'plan' ? '计划模式' : '执行模式'}</span>
                          <span>{message.task.file_count} 个安全文件</span>
                          <span>任务 {message.task.task_id.slice(0, 8)}</span>
                        </div>

                        <div className="agent-plan">
                          <h3>执行计划</h3>
                          <ol>
                            {message.task.plan.map((step) => (
                              <li className={`is-${step.status}`} key={step.id}>
                                <span>{step.position}</span>
                                <div>
                                  <strong>{step.title}</strong>
                                  <p>{step.description}</p>
                                  <small>
                                    {planStatusLabels[step.status]}
                                    {step.tool_name ? ` · ${step.tool_name}` : ''}
                                    {step.requires_confirmation ? ' · 需确认' : ''}
                                  </small>
                                </div>
                              </li>
                            ))}
                          </ol>
                        </div>

                        {message.task.tool_calls.length > 0 && (
                          <div className="agent-tool-calls">
                            <h3>工具调用轨迹</h3>
                            {message.task.tool_calls.map((call) => (
                              <details key={call.id} open={call.status !== 'completed'}>
                                <summary>
                                  <span className={`tool-call-status is-${call.status}`} />
                                  <strong>{call.title}</strong>
                                  <small>
                                    {call.status} · {call.duration_ms?.toFixed(1) ?? '—'} ms
                                  </small>
                                </summary>
                                <div className="tool-call-body">
                                  {Object.keys(call.arguments).length > 0 && (
                                    <code>{JSON.stringify(call.arguments)}</code>
                                  )}
                                  {call.error && <p className="tool-call-error">{call.error}</p>}
                                  {call.result && (
                                    <>
                                      <p>{call.result.summary}</p>
                                      <small>
                                        {call.result.item_count} 项
                                        {call.result.truncated ? ' · 输出已截断' : ''}
                                      </small>
                                      {call.result.evidence.length > 0 && (
                                        <div className="tool-evidence-list">
                                          {call.result.evidence.map((evidence, index) => (
                                            <article
                                              key={`${call.id}:${evidence.file}:${evidence.start_line}:${index}`}
                                            >
                                              <strong>{evidence.file ?? evidence.label}</strong>
                                              <span>
                                                {evidence.file ? evidence.label : ''}
                                                {evidence.start_line
                                                  ? ` · 第 ${evidence.start_line}${
                                                      evidence.end_line &&
                                                      evidence.end_line !== evidence.start_line
                                                        ? `-${evidence.end_line}`
                                                        : ''
                                                    } 行`
                                                  : ''}
                                              </span>
                                              {evidence.preview && <code>{evidence.preview}</code>}
                                            </article>
                                          ))}
                                        </div>
                                      )}
                                    </>
                                  )}
                                </div>
                              </details>
                            ))}
                          </div>
                        )}

                        <details className="agent-transitions">
                          <summary>查看 {message.task.transitions.length} 条状态记录</summary>
                          <ol>
                            {message.task.transitions.map((transition, index) => (
                              <li key={`${transition.at}:${index}`}>
                                <strong>{taskStatusLabels[transition.to_status]}</strong>
                                <span>{transition.reason}</span>
                              </li>
                            ))}
                          </ol>
                        </details>

                        {message.task.warnings.length > 0 && (
                          <details className="warning-panel">
                            <summary>{message.task.warnings.length} 条任务提示</summary>
                            <ul>
                              {message.task.warnings.map((warning, index) => (
                                <li key={`${message.task?.task_id}:warning:${index}`}>
                                  {warning}
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}

                        {(message.task.can_confirm ||
                          message.task.can_cancel ||
                          message.task.can_resume) && (
                          <div className="agent-task-actions">
                            {message.task.can_confirm && (
                              <button
                                type="button"
                                className="is-primary"
                                disabled={busy}
                                onClick={() => void runTaskAction(message.task!, 'confirm')}
                              >
                                确认执行只读计划
                              </button>
                            )}
                            {message.task.can_cancel && (
                              <button
                                type="button"
                                disabled={analysisPending}
                                onClick={() => void runTaskAction(message.task!, 'cancel')}
                              >
                                取消任务
                              </button>
                            )}
                            {message.task.can_resume && (
                              <button
                                type="button"
                                className="is-primary"
                                disabled={busy}
                                onClick={() => void runTaskAction(message.task!, 'resume')}
                              >
                                恢复到待确认
                              </button>
                            )}
                          </div>
                        )}

                        {message.task.status === 'completed' && (
                          <section className="patch-workspace">
                            <div className="patch-workspace-heading">
                              <div>
                                <h3>结构化补丁</h3>
                                <p>
                                  只生成 Diff 草稿；应用仅更新后端内存快照，不覆盖本地原文件。
                                </p>
                                {sandboxStatus && (
                                  <span className={`sandbox-status is-${sandboxStatus.available ? 'available' : 'unavailable'}`}>
                                    <i aria-hidden="true" />
                                    {sandboxStatus.available
                                      ? '隔离沙箱可用'
                                      : sandboxStatus.reason ?? '隔离沙箱不可用'}
                                  </span>
                                )}
                              </div>
                              <button
                                type="button"
                                className="is-primary"
                                disabled={busy}
                                onClick={() => void generatePatch(message.task!)}
                              >
                                生成补丁草稿
                              </button>
                            </div>

                              {patchNotice && patchNoticeTaskId === message.task.task_id && (
                                <div className="patch-success-notice" role="status" aria-live="polite">
                                  <span aria-hidden="true">✓</span>
                                  {patchNotice}
                                </div>
                              )}

                              {(taskPatches[message.task.task_id] ?? []).length === 0 ? (
                              <div className="patch-empty">
                                尚无补丁 · {registeredValidatorCount} 个验证器已注册
                              </div>
                            ) : (
                              <div className="patch-list">
                                {(taskPatches[message.task.task_id] ?? []).map((patch, patchIndex) => (
                                  <article className="patch-card" key={patch.patch_id}>
                                    <div className="patch-card-heading">
                                      <div>
                                        <span className="patch-card-order">补丁 {patchIndex + 1}</span>
                                        <strong>{patch.summary}</strong>
                                        <span>{patchStatusLabels[patch.status]}</span>
                                      </div>
                                      <small>
                                        {patch.created_at.slice(0, 16).replace('T', ' ')} ·{' '}
                                        +{patch.files.reduce((sum, file) => sum + file.additions, 0)}
                                        {' / '}-
                                        {patch.files.reduce((sum, file) => sum + file.deletions, 0)}
                                      </small>
                                    </div>
                                    <p className="patch-risk">风险：{patch.risk}</p>

                                    <div className="patch-files">
                                      {patch.files.map((file) => (
                                        <details className={`patch-file is-${file.decision}`} key={file.file}>
                                          <summary>
                                            <strong>{file.file}</strong>
                                            <span>
                                              +{file.additions} / -{file.deletions} ·{' '}
                                              {file.decision === 'pending'
                                                ? '待审阅'
                                                : file.decision === 'accepted'
                                                  ? '已接受'
                                                  : '已拒绝'}
                                            </span>
                                          </summary>
                                          <div className="patch-file-body">
                                            <p>{file.reason}</p>
                                            <small>
                                              基线 {file.base_version.slice(0, 10)} · 提议{' '}
                                              {file.proposed_version.slice(0, 10)}
                                            </small>
                                            <pre className="patch-diff">
                                              {file.unified_diff.split('\n').map((line, index) => (
                                                <code
                                                  className={
                                                    line.startsWith('+') && !line.startsWith('+++')
                                                      ? 'is-addition'
                                                      : line.startsWith('-') &&
                                                          !line.startsWith('---')
                                                        ? 'is-deletion'
                                                        : line.startsWith('@@')
                                                          ? 'is-hunk'
                                                          : ''
                                                  }
                                                  key={`${file.file}:diff:${index}`}
                                                >
                                                  {line || ' '}
                                                </code>
                                              ))}
                                            </pre>
                                            {['draft', 'in_review', 'approved'].includes(
                                              patch.status,
                                            ) && (
                                              <div className="patch-file-actions">
                                                <button
                                                  type="button"
                                                  className="is-accept"
                                                  disabled={busy}
                                                  onClick={() =>
                                                    void reviewPatch(patch, file.file, 'accepted')
                                                  }
                                                >
                                                  接受此文件
                                                </button>
                                                <button
                                                  type="button"
                                                  disabled={busy}
                                                  onClick={() =>
                                                    void reviewPatch(patch, file.file, 'rejected')
                                                  }
                                                >
                                                  拒绝此文件
                                                </button>
                                              </div>
                                            )}
                                            {patch.can_download && (
                                              <button
                                                type="button"
                                                className="patch-download"
                                                disabled={busy}
                                                onClick={() =>
                                                  void downloadPatchedFile(patch, file.file)
                                                }
                                              >
                                                下载当前内存版本
                                              </button>
                                            )}
                                          </div>
                                        </details>
                                      ))}
                                    </div>

                                    {patch.validations.length > 0 && (
                                      <details className="patch-validations" open>
                                        <summary>
                                          最近验证：
                                          {patch.validations.at(-1)?.status === 'passed'
                                            ? '通过'
                                            : patch.validations.at(-1)?.status === 'failed'
                                              ? '失败'
                                              : patch.validations.at(-1)?.status === 'timed_out'
                                                ? '超时'
                                              : '跳过'}
                                        </summary>
                                        <div>
                                          {patch.validations.at(-1)?.checks.map((check) => (
                                            <article className={`is-${check.status}`} key={check.validator}>
                                              <strong>{check.title}</strong>
                                              <span>
                                                {check.status} · {check.duration_ms.toFixed(1)} ms
                                                {check.exit_code !== null
                                                  ? ` · exit ${check.exit_code}`
                                                  : ''}
                                              </span>
                                              <p>{check.output}</p>
                                            </article>
                                          ))}
                                        </div>
                                      </details>
                                    )}

                                    <details className="patch-events">
                                      <summary>{patch.events.length} 条补丁审计记录</summary>
                                      <ol>
                                        {patch.events.map((event, index) => (
                                          <li key={`${event.at}:${index}`}>
                                            <strong>
                                              {event.action} · {event.actor}
                                            </strong>
                                            <span>{event.detail}</span>
                                          </li>
                                        ))}
                                      </ol>
                                    </details>

                                    <div className="patch-actions">
                                      {patch.can_validate && (
                                        <button
                                          type="button"
                                          disabled={busy}
                                          onClick={() => void runPatchAction(patch, 'validate')}
                                        >
                                          运行内置检查
                                        </button>
                                      )}
                                      {patch.can_validate && sandboxValidators.length > 0 && (
                                        <div className="sandbox-validation-control">
                                          <select
                                            value={sandboxValidator}
                                            onChange={(event) => setSandboxValidator(event.target.value)}
                                            aria-label="沙箱验证命令"
                                          >
                                            {sandboxValidators.map((validator) => (
                                              <option value={validator.name} key={validator.name}>
                                                {validator.title}
                                              </option>
                                            ))}
                                          </select>
                                          <button
                                            type="button"
                                            disabled={busy || !sandboxStatus?.available}
                                            onClick={() => void runSandboxValidation(patch)}
                                            title={sandboxStatus?.reason ?? '在隔离容器中运行'}
                                          >
                                            运行沙箱检查
                                          </button>
                                        </div>
                                      )}
                                      {patch.can_apply && (
                                        <button
                                          type="button"
                                          className="is-primary"
                                          disabled={busy}
                                          onClick={() => void runPatchAction(patch, 'apply')}
                                        >
                                          二次确认并应用
                                        </button>
                                      )}
                                      {patch.can_revert && (
                                        <button
                                          type="button"
                                          className="is-danger"
                                          disabled={busy}
                                          onClick={() => void runPatchAction(patch, 'revert')}
                                        >
                                          撤销补丁
                                        </button>
                                      )}
                                      {patch.can_reject && (
                                        <button
                                          type="button"
                                          disabled={busy}
                                          onClick={() => void runPatchAction(patch, 'reject')}
                                        >
                                          拒绝整个补丁
                                        </button>
                                      )}
                                    </div>
                                  </article>
                                ))}
                              </div>
                            )}
                          </section>
                        )}
                      </section>
                    )}
                  </div>
                </article>
              ))}

              {busy && (
                <article className="message message-assistant message-loading">
                  <div className="message-avatar" aria-hidden="true">
                    CX
                  </div>
                  <div className="message-body">
                    <div className="message-heading">
                      <strong>CodeXXX</strong>
                    </div>
                    <div className="thinking-indicator" role="status">
                      <span />
                      <span />
                      <span />
                      {taskPending
                        ? '正在处理 Agent 计划或只读工具轨迹…'
                        : patchPending
                          ? '正在生成、审阅或验证结构化补丁…'
                          : '正在校验文件并构建代码上下文…'}
                    </div>
                  </div>
                </article>
              )}
            </div>
          )}
        </section>

        <section className="composer-area" aria-label="消息输入区域">
          {selectedFiles.length > 0 && (
            <div className="selected-files-panel">
              <div className="selected-files-heading">
                <div>
                  <strong>代码上下文</strong>
                  <span>
                    {selectedFiles.length} 个文件 · {formatFileSize(totalFileSize)}
                  </span>
                </div>
                <button type="button" onClick={clearFiles} disabled={busy}>
                  清空
                </button>
              </div>
              <div className="file-tree" aria-label="项目目录树">
                {renderFileTreeNodes(fileTree)}
              </div>
              <section className="project-index" aria-label="项目索引状态">
                <div className="project-tool-heading">
                  <div>
                    <strong>项目索引</strong>
                    <span>文件元数据、代码切片和基础符号</span>
                  </div>
                  <span className={`index-status is-${indexState.kind}`} role="status">
                    <i aria-hidden="true" />
                    {indexState.kind === 'idle' && '未开始'}
                    {indexState.kind === 'indexing' && '处理中'}
                    {indexState.kind === 'completed' && '已完成'}
                    {indexState.kind === 'partial' && '部分完成'}
                    {indexState.kind === 'failed' && '失败'}
                  </span>
                  <button
                    type="button"
                    onClick={() => void runIndexing()}
                    disabled={
                      indexState.kind === 'indexing' ||
                      busy ||
                      connection.kind !== 'online'
                    }
                  >
                    {indexState.kind === 'indexing'
                      ? '正在索引…'
                      : indexState.kind === 'completed' || indexState.kind === 'partial'
                        ? '重新索引'
                        : '建立索引'}
                  </button>
                </div>

                {indexState.kind === 'idle' && (
                  <p className="project-tool-hint">
                    索引只处理当前明确选择的安全文件，不写入磁盘，也不调用模型。
                  </p>
                )}
                {indexState.kind === 'indexing' && (
                  <div className="project-tool-progress">
                    <span aria-hidden="true" />
                    正在校验文件并建立请求内索引…
                  </div>
                )}
                {indexState.kind === 'failed' && (
                  <div className="project-tool-error" role="alert">
                    {indexState.message}
                  </div>
                )}
                {(indexState.kind === 'completed' || indexState.kind === 'partial') && (
                  <div className="project-index-result" aria-live="polite">
                    <div className="project-index-stats">
                      <span>
                        <strong>{indexState.data.stats.indexed_files}</strong>
                        文件
                      </span>
                      <span>
                        <strong>{indexState.data.stats.chunks}</strong>
                        切片
                      </span>
                      <span>
                        <strong>{indexState.data.stats.symbols}</strong>
                        符号
                      </span>
                      <span>
                        <strong>{indexState.data.stats.content_chars}</strong>
                        字符
                      </span>
                    </div>
                    <details className="project-index-files">
                      <summary>查看 {indexState.data.files.length} 个文件的索引明细</summary>
                      <div>
                        {indexState.data.files.map((file) => (
                          <article key={file.file}>
                            <strong title={file.file}>{file.file}</strong>
                            <span>
                              {file.language} · {file.lines} 行 · {file.chunks} 切片 ·{' '}
                              {file.symbols} 符号
                            </span>
                          </article>
                        ))}
                      </div>
                    </details>
                    {indexState.data.warnings.length > 0 && (
                      <details className="project-tool-warnings">
                        <summary>{indexState.data.warnings.length} 条索引提示</summary>
                        <ul>
                          {indexState.data.warnings.map((warning, index) => (
                            <li key={`${warning}:${index}`}>{warning}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </section>
              <div className="project-search">
                <form
                  className="project-search-form"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void submitSearch()
                  }}
                >
                  <span aria-hidden="true">⌕</span>
                  <input
                    value={searchQuery}
                    onChange={(event) => {
                      setSearchQuery(event.target.value)
                      setSearchError(null)
                    }}
                    maxLength={500}
                    placeholder="在已选项目文件中搜索文本…"
                    aria-label="搜索项目文本"
                    disabled={searchPending || analysisPending}
                  />
                  <button
                    type="submit"
                    disabled={
                      searchPending ||
                      busy ||
                      !searchQuery.trim() ||
                      connection.kind !== 'online'
                    }
                  >
                    {searchPending ? '搜索中…' : '搜索'}
                  </button>
                </form>

                {searchError && <div className="project-search-error">{searchError}</div>}

                {searchResponse && (
                  <div className="project-search-results" aria-live="polite">
                    <div className="project-search-summary">
                      <strong>“{searchResponse.query}”</strong>
                      <span>
                        {searchResponse.stats.matched_files} 个文件 ·{' '}
                        {searchResponse.stats.matched_lines} 行匹配
                      </span>
                    </div>

                    {searchResponse.results.length > 0 ? (
                      <div className="search-result-list">
                        {searchResponse.results.map((result) => (
                          <article
                            className="search-result"
                            key={`${result.file}:${result.line_number}:${result.column}`}
                          >
                            <div className="search-result-heading">
                              <strong>{result.file}</strong>
                              <span>
                                第 {result.line_number} 行 · 第 {result.column} 列
                                {result.match_count > 1
                                  ? ` · 本行 ${result.match_count} 处`
                                  : ''}
                              </span>
                            </div>
                            <pre>
                              {result.before.map((line, index) => (
                                <code key={`before:${index}`}>{line}</code>
                              ))}
                              <code className="is-match">{result.line}</code>
                              {result.after.map((line, index) => (
                                <code key={`after:${index}`}>{line}</code>
                              ))}
                            </pre>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="project-search-empty">当前文件中没有匹配结果。</div>
                    )}

                    {searchResponse.warnings.length > 0 && (
                      <details className="project-search-warnings">
                        <summary>{searchResponse.warnings.length} 条搜索提示</summary>
                        <ul>
                          {searchResponse.warnings.map((warning, index) => (
                            <li key={`${warning}:${index}`}>{warning}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </div>
              <section className="project-symbols" aria-label="基础符号检索">
                <form
                  className="project-symbol-form"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void submitSymbols()
                  }}
                >
                  <span aria-hidden="true">ƒ</span>
                  <input
                    value={symbolQuery}
                    onChange={(event) => {
                      setSymbolQuery(event.target.value)
                      setSymbolError(null)
                    }}
                    maxLength={200}
                    placeholder="按函数、类或类型名称筛选（可留空）…"
                    aria-label="搜索代码符号"
                    disabled={symbolPending || analysisPending}
                  />
                  <button
                    type="submit"
                    disabled={
                      symbolPending ||
                      busy ||
                      connection.kind !== 'online'
                    }
                  >
                    {symbolPending ? '检索中…' : '检索符号'}
                  </button>
                </form>

                {symbolError && <div className="project-tool-error">{symbolError}</div>}

                {symbolResponse && (
                  <div className="project-symbol-results" aria-live="polite">
                    <div className="project-search-summary">
                      <strong>
                        {symbolResponse.query ? `“${symbolResponse.query}”` : '全部基础符号'}
                      </strong>
                      <span>
                        {symbolResponse.stats.symbol_files} 个文件 ·{' '}
                        {symbolResponse.stats.symbols} 个符号
                        {symbolResponse.truncated ? ' · 已截断' : ''}
                      </span>
                    </div>

                    {symbolResponse.symbols.length > 0 ? (
                      <div className="symbol-result-list">
                        {symbolResponse.symbols.map((symbol, index) => (
                          <article
                            className="symbol-result"
                            key={`${symbol.file}:${symbol.line_number}:${symbol.kind}:${symbol.name}:${index}`}
                          >
                            <div className="symbol-result-heading">
                              <span className={`symbol-kind is-${symbol.kind}`}>
                                {symbol.kind}
                              </span>
                              <strong>{symbol.name}</strong>
                              <small>
                                {symbol.file} · 第 {symbol.line_number} 行
                              </small>
                            </div>
                            <code>{symbol.declaration}</code>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="project-search-empty">当前文件中没有匹配的基础符号。</div>
                    )}

                    {symbolResponse.warnings.length > 0 && (
                      <details className="project-tool-warnings">
                        <summary>{symbolResponse.warnings.length} 条符号检索提示</summary>
                        <ul>
                          {symbolResponse.warnings.map((warning, index) => (
                            <li key={`${warning}:${index}`}>{warning}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </section>
              <div className="selected-files-list">
                {selectedFiles.map((file) => (
                  <div className="selected-file" key={getFileKey(file)}>
                    <span className="selected-file-icon" aria-hidden="true">
                      {'</>'}
                    </span>
                    <span className="selected-file-info">
                      <strong title={getFilePath(file)}>{getFilePath(file)}</strong>
                      <small>
                        {getFileLanguage(getFilePath(file))} · {formatFileSize(file.size)}
                      </small>
                    </span>
                    <button
                      type="button"
                      onClick={() => removeFile(file)}
                      disabled={busy}
                      aria-label={`移除 ${getFilePath(file)}`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(fileNotice || fileRejections.length > 0) && (
            <div className="file-feedback" role="status">
              {fileNotice && <span>{fileNotice}</span>}
              {fileRejections.length > 0 && (
                <details>
                  <summary>查看未载入文件</summary>
                  <ul>
                    {fileRejections.map((rejection, index) => (
                      <li key={`${rejection.name}:${index}`}>
                        <strong>{rejection.name}</strong>：{rejection.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {analysisError && (
            <div className="analysis-error" role="alert">
              <span aria-hidden="true">!</span>
              <span>{analysisError}</span>
              <button type="button" onClick={() => setAnalysisError(null)}>
                ×
              </button>
            </div>
          )}

          <form className="composer-shell" onSubmit={handleComposerSubmit}>
            <div className="composer-mode-switch" aria-label="交互模式">
              <button
                type="button"
                className={interactionMode === 'agent' ? 'is-active' : ''}
                onClick={() => setInteractionMode('agent')}
                disabled={busy}
              >
                Agent 计划
              </button>
              <button
                type="button"
                className={interactionMode === 'analysis' ? 'is-active' : ''}
                onClick={() => setInteractionMode('analysis')}
                disabled={busy}
              >
                直接分析
              </button>
              <span>
                {interactionMode === 'agent'
                  ? `${registeredToolCount} 个只读工具 · 提交后先确认计划`
                  : '使用现有模型分析接口'}
              </span>
            </div>
            <textarea
              aria-label="向 CodeXXX 发送消息"
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value)
                if (analysisError) {
                  setAnalysisError(null)
                }
              }}
              onKeyDown={handleQuestionKeyDown}
              disabled={busy}
              maxLength={8000}
              placeholder={
                interactionMode === 'agent'
                  ? '描述任务，CodeXXX 会先生成只读执行计划…'
                  : '向 CodeXXX 提交直接代码分析问题…'
              }
              rows={3}
            />
            <div className="composer-toolbar">
              <div className="composer-tools">
                <input
                  ref={fileInputRef}
                  className="visually-hidden"
                  type="file"
                  multiple
                  disabled={busy}
                  onChange={handleFileSelection}
                  aria-label="选择代码文件"
                />
                <input
                  ref={directoryInputRef}
                  className="visually-hidden"
                  type="file"
                  multiple
                  disabled={busy}
                  onChange={(event) => handleFileSelection(event, 'directory')}
                  aria-label="选择项目目录"
                  {...({ webkitdirectory: '' } as Record<string, string>)}
                />
                <button
                  type="button"
                  onClick={openFilePicker}
                  disabled={busy}
                  aria-label="添加代码文件"
                >
                  <span aria-hidden="true">＋</span>
                </button>
                <button
                  className="context-button"
                  type="button"
                  onClick={openFilePicker}
                  disabled={busy}
                >
                  <span aria-hidden="true">⌘</span>
                  添加代码文件
                </button>
                <button
                  className="context-button"
                  type="button"
                  onClick={openDirectoryPicker}
                  disabled={busy}
                >
                  <span aria-hidden="true">▦</span>
                  选择项目目录
                </button>
                <span className="file-count">{selectedFiles.length} 个文件</span>
              </div>
              <button
                className="send-button"
                type="submit"
                disabled={
                  busy ||
                  !question.trim() ||
                  selectedFiles.length === 0 ||
                  (interactionMode === 'agent' && !activeProjectId) ||
                  connection.kind !== 'online'
                }
                aria-label="发送消息"
              >
                {busy ? '…' : interactionMode === 'agent' ? '▶' : '↑'}
              </button>
            </div>
          </form>

          <div className="composer-meta">
            <span>
              {interactionMode === 'agent'
                ? '默认计划模式，确认后仅执行只读工具'
                : '代码上下文由你控制'}
            </span>
            <span>{connection.message}</span>
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
