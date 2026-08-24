import { useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  addProjectMember,
  addOrganizationMember,
  createOrganization,
  createProject,
  deleteAccount,
  deleteProject,
  fetchProjectMembers,
  fetchOrganizationMembers,
  fetchRetentionPolicy,
  loginAccount,
  logoutAccount,
  registerAccount,
  removeProjectMember,
  removeOrganizationMember,
  setAccessToken,
  updateProject,
  updateOrganization,
  updateProjectMember,
  updateOrganizationMember,
} from './api'
import type {
  AuthResponse,
  OrganizationMemberResponse,
  OrganizationResponse,
  ProjectMemberResponse,
  ProjectResponse,
  RetentionPolicyResponse,
  UserResponse,
} from './types'

type AuthScreenProps = {
  backendOnline: boolean
  onAuthenticated: (auth: AuthResponse) => Promise<void>
}

export function AuthScreen({ backendOnline, onAuthenticated }: AuthScreenProps) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (pending || !backendOnline) {
      return
    }
    if (mode === 'register') {
      if (password.length < 8) {
        setError('密码长度不能少于 8 位。')
        return
      }
      if (password !== confirmPassword) {
        setError('两次输入的密码不同，请重新输入！')
        return
      }
    }
    setPending(true)
    setError(null)
    try {
      if (mode === 'register') {
        await registerAccount(email, displayName, password)
      }
      const auth = await loginAccount(email, password)
      setAccessToken(auth.access_token)
      await onAuthenticated(auth)
    } catch (reason) {
      setAccessToken(null)
      setError(reason instanceof Error ? reason.message : '认证失败，请稍后重试。')
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-panel" aria-labelledby="auth-title">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden="true">
            C<span>X</span>
          </span>
          <strong>CodeXXX</strong>
        </div>
        <div className="auth-heading">
          <h1 id="auth-title">{mode === 'login' ? '登录工作区' : '创建账号'}</h1>
          <span className={`auth-backend-status is-${backendOnline ? 'online' : 'offline'}`}>
            <i aria-hidden="true" />
            {backendOnline ? '后端已连接' : '后端未连接'}
          </span>
        </div>
        <div className="auth-mode" aria-label="认证方式">
          <button
            type="button"
            className={mode === 'login' ? 'is-active' : ''}
            onClick={() => {
              setMode('login')
              setError(null)
              setConfirmPassword('')
            }}
          >
            登录
          </button>
          <button
            type="button"
            className={mode === 'register' ? 'is-active' : ''}
            onClick={() => {
              setMode('register')
              setError(null)
              setConfirmPassword('')
            }}
          >
            注册
          </button>
        </div>
        <form className="auth-form" onSubmit={submit}>
          {mode === 'register' && (
            <label>
              <span>显示名称</span>
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                minLength={1}
                maxLength={80}
                autoComplete="name"
                required
              />
            </label>
          )}
          <label>
            <span>邮箱</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              maxLength={254}
              autoComplete="email"
              required
            />
          </label>
          <label>
            <span>密码</span>
            {mode === 'register' && (
              <small className="password-hint">密码长度不低于八位</small>
            )}
            <div className="password-input-wrap">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={mode === 'register' ? 8 : 1}
                maxLength={128}
                autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                required
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
              >
                {showPassword ? '隐藏' : '显示'}
              </button>
            </div>
          </label>
          {mode === 'register' && (
            <label>
              <span>再次输入密码</span>
              <div className="password-input-wrap">
                <input
                  type={showConfirmPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  minLength={8}
                  maxLength={128}
                  autoComplete="new-password"
                  required
                />
                <button
                  type="button"
                  className="password-toggle"
                  onClick={() => setShowConfirmPassword((visible) => !visible)}
                  aria-label={showConfirmPassword ? '隐藏确认密码' : '显示确认密码'}
                >
                  {showConfirmPassword ? '隐藏' : '显示'}
                </button>
              </div>
            </label>
          )}
          {error && <div className="access-error" role="alert">{error}</div>}
          <button
            className="auth-submit"
            type="submit"
            disabled={
              pending ||
              !backendOnline ||
              !email.trim() ||
              !password ||
              (mode === 'register' &&
                (!displayName.trim() ||
                  !confirmPassword ||
                  password.length < 8 ||
                  password !== confirmPassword))
            }
          >
            {pending ? '处理中…' : mode === 'login' ? '登录' : '注册并登录'}
          </button>
        </form>
      </section>
    </main>
  )
}

type WorkspaceControlsProps = {
  user: UserResponse
  organizations: OrganizationResponse[]
  activeOrganizationId: string | null
  onActiveOrganizationChange: (organizationId: string) => void
  projects: ProjectResponse[]
  activeProjectId: string | null
  onActiveProjectChange: (projectId: string) => void
  onProjectSaved: (project: ProjectResponse, makeActive: boolean) => void
  onOrganizationSaved: (organization: OrganizationResponse, makeActive: boolean) => void
  onProjectDeleted: (projectId: string) => void
  onSessionEnded: () => void
}

const roleLabels = {
  owner: '所有者',
  admin: '管理员',
  editor: '编辑者',
  viewer: '只读成员',
} as const

export function WorkspaceControls({
  user,
  organizations,
  activeOrganizationId,
  onActiveOrganizationChange,
  projects,
  activeProjectId,
  onActiveProjectChange,
  onProjectSaved,
  onOrganizationSaved,
  onProjectDeleted,
  onSessionEnded,
}: WorkspaceControlsProps) {
  const activeProject = useMemo(
    () => projects.find((project) => project.project_id === activeProjectId) ?? null,
    [activeProjectId, projects],
  )
  const activeOrganization = useMemo(
    () => organizations.find((organization) => organization.organization_id === activeOrganizationId) ?? null,
    [activeOrganizationId, organizations],
  )
  const [open, setOpen] = useState(projects.length === 0)
  const [tab, setTab] = useState<'project' | 'members' | 'organization' | 'account'>('project')
  const [creating, setCreating] = useState(projects.length === 0)
  const [name, setName] = useState(activeProject?.name ?? '')
  const [description, setDescription] = useState(activeProject?.description ?? '')
  const [archived, setArchived] = useState(Boolean(activeProject?.archived_at))
  const [memberEmail, setMemberEmail] = useState('')
  const [memberRole, setMemberRole] = useState<'admin' | 'editor' | 'viewer'>('viewer')
  const [members, setMembers] = useState<ProjectMemberResponse[]>([])
  const [organizationMembers, setOrganizationMembers] = useState<OrganizationMemberResponse[]>([])
  const [organizationMemberEmail, setOrganizationMemberEmail] = useState('')
  const [organizationMemberRole, setOrganizationMemberRole] = useState<'admin' | 'member'>('member')
  const [organizationName, setOrganizationName] = useState(activeOrganization?.name ?? '')
  const [organizationDescription, setOrganizationDescription] = useState(activeOrganization?.description ?? '')
  const [creatingOrganization, setCreatingOrganization] = useState(false)
  const [retention, setRetention] = useState<RetentionPolicyResponse | null>(null)
  const [deleteProjectName, setDeleteProjectName] = useState('')
  const [deleteEmail, setDeleteEmail] = useState('')
  const [deletePassword, setDeletePassword] = useState('')
  const [showDeletePassword, setShowDeletePassword] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      return
    }
    const controller = new AbortController()
    void fetchRetentionPolicy(controller.signal)
      .then(setRetention)
      .catch(() => undefined)
    if (activeProject) {
      void fetchProjectMembers(activeProject.project_id, controller.signal)
        .then((result) => setMembers(result.members))
        .catch((reason: unknown) => {
          if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
            setError(reason instanceof Error ? reason.message : '成员列表加载失败。')
          }
        })
    }
    if (activeOrganization) {
      void fetchOrganizationMembers(activeOrganization.organization_id, controller.signal)
        .then((result) => setOrganizationMembers(result.members))
        .catch(() => undefined)
    }
    return () => controller.abort()
  }, [activeOrganization, activeProject, open])

  const run = async (operation: () => Promise<void>) => {
    if (pending) {
      return
    }
    setPending(true)
    setError(null)
    try {
      await operation()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败，请稍后重试。')
    } finally {
      setPending(false)
    }
  }

  const saveProject = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void run(async () => {
      if (creating) {
        const project = await createProject({
          name,
          description,
          run_mode: 'local',
          organization_id: activeOrganization?.organization_id ?? null,
        })
        onProjectSaved(project, true)
        setCreating(false)
      } else if (activeProject) {
        const project = await updateProject(activeProject.project_id, {
          name,
          description,
          archived,
        })
        onProjectSaved(project, false)
      }
    })
  }

  const refreshMembers = async () => {
    if (!activeProject) {
      return
    }
    const result = await fetchProjectMembers(activeProject.project_id)
    setMembers(result.members)
  }

  const canManage = Boolean(activeProject?.permissions.includes('manage'))
  const canDelete = Boolean(activeProject?.permissions.includes('delete'))
  const canManageOrganization = activeOrganization?.role === 'owner' || activeOrganization?.role === 'admin'

  return (
    <>
      <section className="workspace-switcher" aria-label="当前组织和项目">
        <div>
          <span className="workspace-icon" aria-hidden="true">▦</span>
          <select
            value={activeOrganizationId ?? ''}
            onChange={(event) => onActiveOrganizationChange(event.target.value)}
            disabled={organizations.length === 0}
            aria-label="切换组织"
          >
            {organizations.length === 0 && <option value="">尚无组织</option>}
            {organizations.map((organization) => (
              <option value={organization.organization_id} key={organization.organization_id}>{organization.name}</option>
            ))}
          </select>
          <select
            value={activeProjectId ?? ''}
            onChange={(event) => onActiveProjectChange(event.target.value)}
            disabled={projects.length === 0}
            aria-label="切换项目"
          >
            {projects.length === 0 && <option value="">尚无项目</option>}
            {projects.map((project) => (
              <option value={project.project_id} key={project.project_id}>
                {project.name}{project.archived_at ? '（已归档）' : ''}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => {
            setOpen(true)
            setCreating(false)
            setError(null)
          }}
          aria-label="管理项目和账号"
          title="管理项目和账号"
        >
          ⋯
        </button>
      </section>

      <button
        className="account-summary"
        type="button"
        onClick={() => {
          setOpen(true)
          setTab('account')
          setError(null)
        }}
      >
        <span>{user.display_name.slice(0, 1).toUpperCase()}</span>
        <span>
          <strong>{user.display_name}</strong>
          <small>{user.email}</small>
        </span>
      </button>

      {open && (
        <div className="access-modal-backdrop" role="presentation">
          <section className="access-modal" role="dialog" aria-modal="true" aria-label="工作区设置">
            <header>
              <div>
                <strong>工作区设置</strong>
                <span>{activeProject?.name ?? '创建首个项目'}</span>
              </div>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭">×</button>
            </header>
            <div className="access-tabs" role="tablist">
              <button
                type="button"
                className={tab === 'project' ? 'is-active' : ''}
                onClick={() => setTab('project')}
              >
                项目
              </button>
              <button
                type="button"
                className={tab === 'members' ? 'is-active' : ''}
                onClick={() => setTab('members')}
                disabled={!activeProject}
              >
                成员
              </button>
              <button
                type="button"
                className={tab === 'account' ? 'is-active' : ''}
                onClick={() => setTab('account')}
              >
                账号
              </button>
              <button
                type="button"
                className={tab === 'organization' ? 'is-active' : ''}
                onClick={() => setTab('organization')}
                disabled={!activeOrganization}
              >
                组织
              </button>
            </div>

            <div className="access-modal-body">
              {tab === 'project' && (
                <>
                  <div className="settings-section-heading">
                    <div>
                      <strong>{creating ? '新建项目' : '项目设置'}</strong>
                      {!creating && activeProject && <span>{roleLabels[activeProject.role]}</span>}
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setCreating(!creating)
                        setName(creating ? activeProject?.name ?? '' : '')
                        setDescription(creating ? activeProject?.description ?? '' : '')
                        setError(null)
                      }}
                    >
                      {creating ? '返回当前项目' : '新建项目'}
                    </button>
                  </div>
                  <form className="settings-form" onSubmit={saveProject}>
                    <label>
                      <span>项目名称</span>
                      <input
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        maxLength={120}
                        disabled={!creating && !canManage}
                        required
                      />
                    </label>
                    <label>
                      <span>项目描述</span>
                      <textarea
                        value={description}
                        onChange={(event) => setDescription(event.target.value)}
                        maxLength={2000}
                        rows={3}
                        disabled={!creating && !canManage}
                      />
                    </label>
                    {!creating && activeProject && canManage && (
                      <label className="settings-checkbox">
                        <input
                          type="checkbox"
                          checked={archived}
                          onChange={(event) => setArchived(event.target.checked)}
                        />
                        <span>归档项目</span>
                      </label>
                    )}
                    {(creating || canManage) && (
                      <button className="settings-primary" type="submit" disabled={pending || !name.trim()}>
                        {pending ? '保存中…' : creating ? '创建项目' : '保存更改'}
                      </button>
                    )}
                  </form>

                  {!creating && activeProject && canDelete && (
                    <section className="danger-section">
                      <strong>删除项目</strong>
                      <p>任务、文件快照、补丁、验证和项目用量将不可恢复。</p>
                      <input
                        value={deleteProjectName}
                        onChange={(event) => setDeleteProjectName(event.target.value)}
                        placeholder={activeProject.name}
                        aria-label="输入项目名称确认删除"
                      />
                      <button
                        type="button"
                        disabled={pending || deleteProjectName !== activeProject.name}
                        onClick={() => void run(async () => {
                          await deleteProject(activeProject.project_id, deleteProjectName)
                          onProjectDeleted(activeProject.project_id)
                          setDeleteProjectName('')
                        })}
                      >
                        删除项目
                      </button>
                    </section>
                  )}
                </>
              )}

              {tab === 'members' && activeProject && (
                <section className="members-section">
                  <div className="settings-section-heading">
                    <div>
                      <strong>项目成员</strong>
                      <span>{members.length} 人</span>
                    </div>
                  </div>
                  {canManage && (
                    <form
                      className="member-invite"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void run(async () => {
                          await addProjectMember(activeProject.project_id, memberEmail, memberRole)
                          setMemberEmail('')
                          await refreshMembers()
                        })
                      }}
                    >
                      <input
                        type="email"
                        value={memberEmail}
                        onChange={(event) => setMemberEmail(event.target.value)}
                        placeholder="成员邮箱"
                        required
                      />
                      <select
                        value={memberRole}
                        onChange={(event) => setMemberRole(event.target.value as typeof memberRole)}
                      >
                        <option value="viewer">只读成员</option>
                        <option value="editor">编辑者</option>
                        <option value="admin">管理员</option>
                      </select>
                      <button type="submit" disabled={pending || !memberEmail.trim()}>添加</button>
                    </form>
                  )}
                  <div className="member-list">
                    {members.map((member) => (
                      <div className="member-row" key={member.user_id}>
                        <span>{member.display_name.slice(0, 1).toUpperCase()}</span>
                        <span>
                          <strong>{member.display_name}</strong>
                          <small>{member.email}</small>
                        </span>
                        {member.role === 'owner' || !canManage ? (
                          <small>{roleLabels[member.role]}</small>
                        ) : (
                          <select
                            value={member.role}
                            disabled={pending}
                            onChange={(event) => void run(async () => {
                              await updateProjectMember(
                                activeProject.project_id,
                                member.user_id,
                                event.target.value as 'admin' | 'editor' | 'viewer',
                              )
                              await refreshMembers()
                            })}
                          >
                            <option value="viewer">只读成员</option>
                            <option value="editor">编辑者</option>
                            <option value="admin">管理员</option>
                          </select>
                        )}
                        {member.role !== 'owner' && canManage && (
                          <button
                            type="button"
                            aria-label={`移除 ${member.display_name}`}
                            title={`移除 ${member.display_name}`}
                            disabled={pending}
                            onClick={() => void run(async () => {
                              await removeProjectMember(activeProject.project_id, member.user_id)
                              await refreshMembers()
                            })}
                          >
                            ×
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {tab === 'organization' && activeOrganization && (
                <section className="members-section">
                  <div className="settings-section-heading">
                    <div><strong>组织成员</strong><span>{activeOrganization.name} · {organizationMembers.length} 人</span></div>
                    <button type="button" onClick={() => {
                      if (creatingOrganization) {
                        setOrganizationName(activeOrganization.name)
                        setOrganizationDescription(activeOrganization.description)
                      } else {
                        setOrganizationName('')
                        setOrganizationDescription('')
                      }
                      setCreatingOrganization(!creatingOrganization)
                      setError(null)
                    }}>{creatingOrganization ? '返回当前组织' : '新建组织'}</button>
                  </div>
                  {creatingOrganization && (
                    <form className="settings-form" onSubmit={(event) => {
                      event.preventDefault()
                      void run(async () => {
                        const organization = await createOrganization({ name: organizationName, description: organizationDescription })
                        onOrganizationSaved(organization, true)
                        setCreatingOrganization(false)
                      })
                    }}>
                      <label><span>新组织名称</span><input value={organizationName} onChange={(event) => setOrganizationName(event.target.value)} maxLength={120} required /></label>
                      <label><span>组织描述</span><textarea value={organizationDescription} onChange={(event) => setOrganizationDescription(event.target.value)} maxLength={2000} rows={2} /></label>
                      <button className="settings-primary" type="submit" disabled={pending || !organizationName.trim()}>创建组织</button>
                    </form>
                  )}
                  {!creatingOrganization && canManageOrganization && (
                    <form className="settings-form" onSubmit={(event) => {
                      event.preventDefault()
                      void run(async () => {
                        const organization = await updateOrganization(activeOrganization.organization_id, { name: organizationName, description: organizationDescription })
                        onOrganizationSaved(organization, false)
                      })
                    }}>
                      <label><span>组织名称</span><input value={organizationName} onChange={(event) => setOrganizationName(event.target.value)} maxLength={120} required /></label>
                      <label><span>组织描述</span><textarea value={organizationDescription} onChange={(event) => setOrganizationDescription(event.target.value)} maxLength={2000} rows={2} /></label>
                      <button className="settings-primary" type="submit" disabled={pending || !organizationName.trim()}>保存组织</button>
                    </form>
                  )}
                  {!creatingOrganization && canManageOrganization && (
                    <form className="member-invite" onSubmit={(event) => {
                      event.preventDefault()
                      void run(async () => {
                        await addOrganizationMember(activeOrganization.organization_id, organizationMemberEmail, organizationMemberRole)
                        setOrganizationMemberEmail('')
                        const result = await fetchOrganizationMembers(activeOrganization.organization_id)
                        setOrganizationMembers(result.members)
                      })
                    }}>
                      <input type="email" value={organizationMemberEmail} onChange={(event) => setOrganizationMemberEmail(event.target.value)} placeholder="成员邮箱" required />
                      <select value={organizationMemberRole} onChange={(event) => setOrganizationMemberRole(event.target.value as typeof organizationMemberRole)}><option value="member">成员</option><option value="admin">管理员</option></select>
                      <button type="submit" disabled={pending || !organizationMemberEmail.trim()}>添加</button>
                    </form>
                  )}
                  {!creatingOrganization && <div className="member-list">
                    {organizationMembers.map((member) => (
                      <div className="member-row" key={member.user_id}>
                        <span>{member.display_name.slice(0, 1).toUpperCase()}</span>
                        <span><strong>{member.display_name}</strong><small>{member.email}</small></span>
                        {member.role === 'owner' || !canManageOrganization ? <small>{member.role === 'owner' ? '所有者' : member.role === 'admin' ? '管理员' : '成员'}</small> : <select value={member.role} disabled={pending} onChange={(event) => void run(async () => {
                          await updateOrganizationMember(activeOrganization.organization_id, member.user_id, event.target.value as 'admin' | 'member')
                          const result = await fetchOrganizationMembers(activeOrganization.organization_id)
                          setOrganizationMembers(result.members)
                        })}><option value="member">成员</option><option value="admin">管理员</option></select>}
                        {member.role !== 'owner' && canManageOrganization && <button type="button" disabled={pending} onClick={() => void run(async () => {
                          await removeOrganizationMember(activeOrganization.organization_id, member.user_id)
                          setOrganizationMembers((current) => current.filter((item) => item.user_id !== member.user_id))
                        })} aria-label={`移除 ${member.display_name}`}>×</button>}
                      </div>
                    ))}
                  </div>}
                </section>
              )}

              {tab === 'account' && (
                <section className="account-section">
                  <div className="account-identity">
                    <span>{user.display_name.slice(0, 1).toUpperCase()}</span>
                    <div>
                      <strong>{user.display_name}</strong>
                      <small>{user.email}</small>
                    </div>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() => void run(async () => {
                        await logoutAccount()
                        setAccessToken(null)
                        onSessionEnded()
                      })}
                    >
                      退出登录
                    </button>
                  </div>

                  {retention && (
                    <section className="retention-grid">
                      <strong>数据保留</strong>
                      <span><span className="retention-value"><b>{retention.terminal_tasks_days}</b><em>天</em></span><small>任务、补丁与验证</small></span>
                      <span><span className="retention-value"><b>{retention.audit_logs_days}</b><em>天</em></span><small>审计日志</small></span>
                      <span><span className="retention-value"><b>{retention.usage_records_days}</b><em>天</em></span><small>用量记录</small></span>
                      <span><span className="retention-value"><b>{retention.expired_sessions_days}</b><em>天</em></span><small>过期会话</small></span>
                    </section>
                  )}

                  <section className="danger-section">
                    <strong>删除账号</strong>
                    <p>所属项目、会话、任务、补丁和用量将不可恢复；审计记录会匿名保留。</p>
                    <input
                      type="email"
                      value={deleteEmail}
                      onChange={(event) => setDeleteEmail(event.target.value)}
                      placeholder={user.email}
                      aria-label="输入账号邮箱确认删除"
                    />
                    <div className="password-input-wrap">
                      <input
                        type={showDeletePassword ? 'text' : 'password'}
                        value={deletePassword}
                        onChange={(event) => setDeletePassword(event.target.value)}
                        placeholder="当前密码"
                        aria-label="当前密码"
                      />
                      <button
                        type="button"
                        className="password-toggle"
                        onClick={() => setShowDeletePassword((visible) => !visible)}
                        aria-label={showDeletePassword ? '隐藏当前密码' : '显示当前密码'}
                      >
                        {showDeletePassword ? '隐藏' : '显示'}
                      </button>
                    </div>
                    <button
                      type="button"
                      disabled={pending || deleteEmail.trim().toLowerCase() !== user.email || !deletePassword}
                      onClick={() => void run(async () => {
                        await deleteAccount(deleteEmail, deletePassword)
                        setAccessToken(null)
                        onSessionEnded()
                      })}
                    >
                      永久删除账号
                    </button>
                  </section>
                </section>
              )}

              {error && <div className="access-error" role="alert">{error}</div>}
            </div>
          </section>
        </div>
      )}
    </>
  )
}
