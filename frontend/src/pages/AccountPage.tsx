import { useEffect, useState, type FormEvent } from 'react'
import { KeyRound, Shield, User as UserIcon, Users } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '@/shared/auth/AuthContext'
import { useAccountMutation, useUserMutation, useUsersQuery } from '@/hooks/queries'
import type { User, UserRole } from '@/types'
import { PageHeader } from '@/shared/components/PageHeader'
import { Avatar, AvatarFallback } from '@/shared/ui/avatar'
import { Badge } from '@/shared/ui/badge'
import { Button } from '@/shared/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { Label } from '@/shared/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'

const ASSIGNABLE_ROLES: UserRole[] = ['viewer', 'operator', 'admin', 'super-admin']

export function AccountPage() {
  const { user, isAdmin, isSuperAdmin, applySession } = useAuth()
  const accountMutation = useAccountMutation()
  const userMutation = useUserMutation()
  const usersQuery = useUsersQuery(isAdmin)

  const [username, setUsername] = useState(user?.username ?? '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<string | null>(null)
  const [editUsername, setEditUsername] = useState('')
  const [editPassword, setEditPassword] = useState('')
  const [editRole, setEditRole] = useState<UserRole>('viewer')
  const [adminError, setAdminError] = useState<string | null>(null)

  useEffect(() => {
    setUsername(user?.username ?? '')
  }, [user?.username])

  const onSaveAccount = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)

    if (newPassword && newPassword !== confirmPassword) {
      setError('New password and confirmation do not match')
      return
    }
    if (!currentPassword) {
      setError('Current password is required')
      return
    }

    const nextUsername = username.trim()
    const usernameChanged = nextUsername !== (user?.username ?? '')
    if (user?.mustChangePassword && !newPassword) {
      setError('You must set a new password before continuing')
      return
    }
    if (!usernameChanged && !newPassword) {
      setError('Enter a new username and/or new password')
      return
    }

    try {
      const res = await accountMutation.mutateAsync({
        currentPassword,
        username: usernameChanged ? nextUsername : undefined,
        newPassword: newPassword || undefined,
      })
      applySession(res.token, res.user)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      toast.success(res.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update account')
    }
  }

  const openEdit = (target: User) => {
    setEditingId(target._id)
    setEditUsername(target.username)
    setEditPassword('')
    setEditRole(target.role)
    setAdminError(null)
  }

  const onAdminSave = async (event: FormEvent) => {
    event.preventDefault()
    if (!editingId) return
    setAdminError(null)

    const payload: { username?: string; password?: string; role?: UserRole } = {}
    const original = usersQuery.data?.find((u) => u._id === editingId)
    if (editUsername.trim() && editUsername.trim() !== original?.username) {
      payload.username = editUsername.trim()
    }
    if (editPassword.trim()) payload.password = editPassword.trim()
    if (editRole && editRole !== original?.role) payload.role = editRole

    if (!payload.username && !payload.password && !payload.role) {
      setAdminError('Change username, password, and/or role before saving')
      return
    }

    try {
      await userMutation.mutateAsync({ id: editingId, payload })
      setEditingId(null)
      setEditPassword('')
    } catch (err) {
      setAdminError(err instanceof Error ? err.message : 'Failed to update user')
    }
  }

  const initials = (user?.username || 'U').slice(0, 2).toUpperCase()

  return (
    <div className="np-page">
      <PageHeader title="Account" description="Manage your profile, security, and preferences" />

      {user?.mustChangePassword ? (
        <div
          className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
          role="alert"
        >
          Password change required. Set a new password before using the rest of the application.
        </div>
      ) : null}

      <section className="grid gap-4 lg:grid-cols-3">
        <Card className="glass lg:col-span-1">
          <CardContent className="flex flex-col items-center gap-4 py-8 text-center">
            <Avatar className="h-20 w-20 text-xl">
              <AvatarFallback className="text-xl">{initials}</AvatarFallback>
            </Avatar>
            <div>
              <p className="text-xl font-bold">{user?.username}</p>
              <Badge variant="secondary" className="mt-2 capitalize">
                {user?.role}
              </Badge>
            </div>
            <div className="w-full space-y-2 rounded-lg border border-border/60 bg-secondary/30 p-3 text-left text-sm">
              <p className="flex items-center gap-2 text-muted-foreground">
                <Shield className="h-4 w-4" />
                Session active
              </p>
              <p className="flex items-center gap-2 text-muted-foreground">
                <UserIcon className="h-4 w-4" />
                Role: <span className="capitalize text-foreground">{user?.role}</span>
              </p>
            </div>
          </CardContent>
        </Card>

        <Card className="glass lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <KeyRound className="h-5 w-5 text-primary" />
              Security & credentials
            </CardTitle>
            <CardDescription>Update your username or password. Current password is required.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={(e) => void onSaveAccount(e)}>
              {error ? (
                <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
                  {error}
                </div>
              ) : null}
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="username">Username</Label>
                  <Input
                    id="username"
                    required
                    minLength={3}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="currentPassword">Current password</Label>
                  <Input
                    id="currentPassword"
                    type="password"
                    required
                    autoComplete="current-password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="newPassword">
                    {user?.mustChangePassword ? 'New password (required)' : 'New password (optional)'}
                  </Label>
                  <Input
                    id="newPassword"
                    type="password"
                    autoComplete="new-password"
                    required={Boolean(user?.mustChangePassword)}
                    minLength={6}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="confirmPassword">Confirm new password</Label>
                  <Input
                    id="confirmPassword"
                    type="password"
                    minLength={6}
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex justify-end">
                <Button type="submit" disabled={accountMutation.isPending}>
                  {accountMutation.isPending ? 'Saving…' : 'Save account'}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </section>

      {isAdmin ? (
        <Card className="glass">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Users className="h-5 w-5 text-primary" />
              Manage users
            </CardTitle>
            <CardDescription>
              Admins can reset usernames and passwords. Super-admins can also assign roles.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {adminError ? (
              <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
                {adminError}
              </div>
            ) : null}

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Username</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(usersQuery.data ?? []).map((row) => (
                  <TableRow key={row._id}>
                    <TableCell className="font-semibold">{row.username}</TableCell>
                    <TableCell className="capitalize text-muted-foreground">{row.role}</TableCell>
                    <TableCell>
                      <Button type="button" size="sm" variant="secondary" onClick={() => openEdit(row)}>
                        Edit
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {editingId ? (
              <form
                className="space-y-4 rounded-xl border border-border bg-secondary/20 p-4"
                onSubmit={(e) => void onAdminSave(e)}
              >
                <h3 className="font-semibold">Edit user</h3>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Username</Label>
                    <Input
                      required
                      minLength={3}
                      value={editUsername}
                      onChange={(e) => setEditUsername(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>New password (optional)</Label>
                    <Input
                      type="password"
                      minLength={6}
                      value={editPassword}
                      onChange={(e) => setEditPassword(e.target.value)}
                      placeholder="Leave blank to keep current"
                    />
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label>Role</Label>
                    <Select
                      value={editRole}
                      onValueChange={(value) => setEditRole(value as UserRole)}
                      disabled={!isSuperAdmin && editRole === 'super-admin'}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select role" />
                      </SelectTrigger>
                      <SelectContent>
                        {ASSIGNABLE_ROLES.filter(
                          (role) => isSuperAdmin || role !== 'super-admin',
                        ).map((role) => (
                          <SelectItem key={role} value={role}>
                            {role}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {!isSuperAdmin ? (
                      <p className="text-xs text-muted-foreground">
                        Only a super-admin can assign or edit the super-admin role.
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setEditingId(null)}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={userMutation.isPending}>
                    {userMutation.isPending ? 'Saving…' : 'Update user'}
                  </Button>
                </div>
              </form>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
