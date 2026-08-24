export const MAX_FILE_SIZE_BYTES = 1_048_576
export const MAX_TOTAL_FILE_SIZE_BYTES = 20_971_520
export const MAX_FILE_COUNT = 50

const LANGUAGE_BY_EXTENSION = new Map<string, string>([
  ['.py', 'Python'],
  ['.js', 'JavaScript'],
  ['.jsx', 'JavaScript JSX'],
  ['.ts', 'TypeScript'],
  ['.tsx', 'TypeScript JSX'],
  ['.java', 'Java'],
  ['.c', 'C'],
  ['.h', 'C Header'],
  ['.cpp', 'C++'],
  ['.cc', 'C++'],
  ['.hpp', 'C++ Header'],
  ['.cs', 'C#'],
  ['.go', 'Go'],
  ['.rs', 'Rust'],
  ['.php', 'PHP'],
  ['.rb', 'Ruby'],
  ['.swift', 'Swift'],
  ['.kt', 'Kotlin'],
  ['.kts', 'Kotlin Script'],
  ['.scala', 'Scala'],
  ['.vue', 'Vue'],
  ['.svelte', 'Svelte'],
  ['.html', 'HTML'],
  ['.htm', 'HTML'],
  ['.css', 'CSS'],
  ['.scss', 'SCSS'],
  ['.sass', 'Sass'],
  ['.less', 'Less'],
  ['.json', 'JSON'],
  ['.yaml', 'YAML'],
  ['.yml', 'YAML'],
  ['.toml', 'TOML'],
  ['.xml', 'XML'],
  ['.ini', 'INI'],
  ['.cfg', 'Config'],
  ['.conf', 'Config'],
  ['.md', 'Markdown'],
  ['.txt', 'Text'],
  ['.sql', 'SQL'],
  ['.sh', 'Shell'],
  ['.ps1', 'PowerShell'],
  ['.bat', 'Batch'],
  ['.cmd', 'Command Script'],
  ['.lock', 'Lockfile'],
])

const LANGUAGE_BY_FILENAME = new Map<string, string>([
  ['dockerfile', 'Dockerfile'],
  ['makefile', 'Makefile'],
  ['procfile', 'Procfile'],
  ['.gitignore', 'Git Ignore'],
  ['.dockerignore', 'Docker Ignore'],
  ['.editorconfig', 'EditorConfig'],
])

const BLOCKED_FILE_PATTERNS = [
  /^\.env(?:\..+)?$/i,
  /^id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?$/i,
  /^(?:credentials?|secrets?)(?:\.[^.]+)?$/i,
]

const BLOCKED_EXTENSIONS = new Set(['.pem', '.key', '.p12', '.pfx', '.keystore'])
const IGNORED_PATH_PARTS = new Set([
  '.git',
  '.idea',
  '.vscode',
  '.venv',
  'venv',
  'node_modules',
  'dist',
  'build',
  'coverage',
  '__pycache__',
  '.pytest_cache',
  '.ruff_cache',
])

export type FileRejection = {
  name: string
  reason: string
}

export type FileSelectionResult = {
  files: File[]
  addedCount: number
  duplicateCount: number
  rejections: FileRejection[]
}

export type FileTreeNode =
  | {
      kind: 'directory'
      name: string
      path: string
      children: FileTreeNode[]
    }
  | {
      kind: 'file'
      name: string
      path: string
      file: File
    }

type MutableDirectoryNode = {
  kind: 'mutable-directory'
  name: string
  path: string
  children: Map<string, MutableDirectoryNode | Extract<FileTreeNode, { kind: 'file' }>>
}

export function getFilePath(file: File): string {
  return file.webkitRelativePath || file.name
}

export function getFileKey(file: File): string {
  return `${getFilePath(file)}:${file.size}:${file.lastModified}`
}

export function getFileExtension(fileName: string): string {
  const normalizedName = fileName.toLowerCase()
  const dotIndex = normalizedName.lastIndexOf('.')

  return dotIndex > 0 ? normalizedName.slice(dotIndex) : ''
}

export function getFileLanguage(fileName: string): string | null {
  const normalizedName = fileName.toLowerCase()
  const baseName = normalizedName.split('/').at(-1) ?? normalizedName
  const specialLanguage = LANGUAGE_BY_FILENAME.get(baseName)

  if (specialLanguage) {
    return specialLanguage
  }

  return LANGUAGE_BY_EXTENSION.get(getFileExtension(normalizedName)) ?? null
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getBlockedReason(filePath: string): string | null {
  const normalizedParts = filePath.replaceAll('\\', '/').toLowerCase().split('/')
  const fileName = normalizedParts.at(-1) ?? filePath

  if (normalizedParts.slice(0, -1).some((part) => IGNORED_PATH_PARTS.has(part))) {
    return '位于默认忽略的目录中'
  }

  if (BLOCKED_FILE_PATTERNS.some((pattern) => pattern.test(fileName))) {
    return '疑似包含凭据或密钥'
  }

  if (BLOCKED_EXTENSIONS.has(getFileExtension(fileName))) {
    return '密钥或证书文件不允许载入'
  }

  return null
}

export function selectCodeFiles(
  currentFiles: File[],
  incomingFiles: File[],
): FileSelectionResult {
  const nextFiles = [...currentFiles]
  const existingKeys = new Set(currentFiles.map(getFileKey))
  const rejections: FileRejection[] = []
  let duplicateCount = 0
  let totalSize = currentFiles.reduce((sum, file) => sum + file.size, 0)

  for (const file of incomingFiles) {
    const fileKey = getFileKey(file)
    const filePath = getFilePath(file)

    if (existingKeys.has(fileKey)) {
      duplicateCount += 1
      continue
    }

    const blockedReason = getBlockedReason(filePath)
    if (blockedReason) {
      rejections.push({ name: filePath, reason: blockedReason })
      continue
    }

    if (!getFileLanguage(filePath)) {
      rejections.push({ name: filePath, reason: '不支持的文件类型' })
      continue
    }

    if (file.size > MAX_FILE_SIZE_BYTES) {
      rejections.push({
        name: filePath,
        reason: `超过单文件 ${formatFileSize(MAX_FILE_SIZE_BYTES)} 限制`,
      })
      continue
    }

    if (totalSize + file.size > MAX_TOTAL_FILE_SIZE_BYTES) {
      rejections.push({
        name: filePath,
        reason: `加入后超过 ${formatFileSize(MAX_TOTAL_FILE_SIZE_BYTES)} 总量限制`,
      })
      continue
    }

    if (nextFiles.length >= MAX_FILE_COUNT) {
      rejections.push({
        name: filePath,
        reason: `最多可载入 ${MAX_FILE_COUNT} 个文件`,
      })
      continue
    }

    nextFiles.push(file)
    existingKeys.add(fileKey)
    totalSize += file.size
  }

  return {
    files: nextFiles,
    addedCount: nextFiles.length - currentFiles.length,
    duplicateCount,
    rejections,
  }
}

function compareTreeNodes(left: FileTreeNode, right: FileTreeNode): number {
  if (left.kind !== right.kind) {
    return left.kind === 'directory' ? -1 : 1
  }
  return left.name.localeCompare(right.name, 'zh-CN', { sensitivity: 'base' })
}

function finalizeDirectory(node: MutableDirectoryNode): FileTreeNode {
  const children = Array.from(node.children.values()).map((child) =>
    child.kind === 'mutable-directory' ? finalizeDirectory(child) : child,
  )

  return {
    kind: 'directory',
    name: node.name,
    path: node.path,
    children: children.sort(compareTreeNodes),
  }
}

export function buildFileTree(files: File[]): FileTreeNode[] {
  const root: MutableDirectoryNode = {
    kind: 'mutable-directory',
    name: '',
    path: '',
    children: new Map(),
  }

  for (const file of files) {
    const filePath = getFilePath(file).replaceAll('\\', '/')
    const pathParts = filePath.split('/').filter(Boolean)
    const fileName = pathParts.pop()
    if (!fileName) {
      continue
    }

    let currentDirectory = root
    for (const directoryName of pathParts) {
      const directoryPath = currentDirectory.path
        ? `${currentDirectory.path}/${directoryName}`
        : directoryName
      const existingNode = currentDirectory.children.get(directoryName)

      if (existingNode?.kind === 'mutable-directory') {
        currentDirectory = existingNode
        continue
      }

      const nextDirectory: MutableDirectoryNode = {
        kind: 'mutable-directory',
        name: directoryName,
        path: directoryPath,
        children: new Map(),
      }
      currentDirectory.children.set(directoryName, nextDirectory)
      currentDirectory = nextDirectory
    }

    currentDirectory.children.set(fileName, {
      kind: 'file',
      name: fileName,
      path: filePath,
      file,
    })
  }

  return Array.from(root.children.values())
    .map((node) =>
      node.kind === 'mutable-directory' ? finalizeDirectory(node) : node,
    )
    .sort(compareTreeNodes)
}
