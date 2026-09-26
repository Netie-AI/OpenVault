/**
 * "Add another mailbox" dialog.
 *
 * On the self-hosted build there are no OAuth providers (no Gmail /
 * Outlook linking) - sign-in IS the IMAP login. Adding a connection
 * just means signing in to another mailbox without losing the current
 * one: the server keeps both sessions live in `zero_sessions`, the
 * new mailbox becomes active, and the previous user's IDB cache stays
 * put under its own namespaced slot.
 *
 * Submitting → POST /auth/sign-in (no prior signOut) → hard navigate
 * to /mail/inbox. The hard nav is what re-mounts root.tsx with the new
 * connection id, which in turn picks the new IDB persist key. No
 * per-key invalidation needed.
 */

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../ui/dialog';
import { signIn } from '@/lib/auth-client';
import { Eye, EyeOff, Loader2, Lock, Mail, UserPlus } from 'lucide-react';
import { m } from '@/paraglide/messages';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { cn } from '@/lib/utils';
import { useState } from 'react';
import { toast } from 'sonner';

export const AddConnectionDialog = ({
  children,
  className,
  onOpenChange,
}: {
  children?: React.ReactNode;
  className?: string;
  onOpenChange?: (open: boolean) => void;
}) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    try {
      // No prior signOut - the server adds this session alongside the
      // current one and atomically rotates the active cookie. The other
      // account is still signed in; the operator can switch back from
      // the sidebar.
      const { error } = await signIn.email({ email, password });
      if (error) {
        toast.error(error.message);
        return;
      }
      toast.success('Signed in');
      // Hard navigate so root.tsx re-reads the active-id cookie and
      // mounts the QueryClient under the new mailbox's IDB slot.
      window.location.href = '/mail/inbox';
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Sign in failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        {children || (
          <Button
            size={'dropdownItem'}
            variant={'dropdownItem'}
            className={cn('w-full justify-start gap-2', className)}
          >
            <UserPlus size={16} strokeWidth={2} className="opacity-60" aria-hidden="true" />
            <p className="text-[13px] opacity-60">{m['pages.settings.connections.addEmail']()}</p>
          </Button>
        )}
      </DialogTrigger>
      <DialogContent showOverlay={true} className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Add another mailbox</DialogTitle>
          <DialogDescription>
            Sign in with another mailbox's credentials. Your current account stays signed in -
            switch back any time from the sidebar.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} className="mt-2 space-y-5">
          <div className="space-y-2">
            <Label htmlFor="add-conn-email" className="text-[13px] font-medium text-foreground/85">
              Email
            </Label>
            <div className="relative">
              <Mail
                aria-hidden
                className="pointer-events-none absolute left-4 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-muted-foreground/60"
              />
              <Input
                id="add-conn-email"
                type="email"
                autoComplete="email"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="h-11 rounded-xl pl-11 pr-4 text-[15px]"
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label
              htmlFor="add-conn-password"
              className="text-[13px] font-medium text-foreground/85"
            >
              Password
            </Label>
            <div className="relative">
              <Lock
                aria-hidden
                className="pointer-events-none absolute left-4 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-muted-foreground/60"
              />
              <Input
                id="add-conn-password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                className="h-11 rounded-xl pl-11 pr-12 text-[15px]"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                tabIndex={-1}
                className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted-foreground/60 transition-colors hover:bg-foreground/5 hover:text-foreground"
              >
                {showPassword ? (
                  <EyeOff className="h-[18px] w-[18px]" />
                ) : (
                  <Eye className="h-[18px] w-[18px]" />
                )}
              </button>
            </div>
          </div>

          <Button type="submit" disabled={submitting} className="h-11 w-full rounded-xl text-[14px]">
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Signing in
              </>
            ) : (
              'Sign in'
            )}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
};
