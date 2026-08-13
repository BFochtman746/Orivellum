/**
 * Update-ready prompt (WP5). Renders a small fixed card when a new build is
 * installed and waiting. "Update now" is gated on the app-busy registry — it
 * is disabled (with the reason shown) while a draft, stream, upload or
 * operation is in flight, so an update can never lose work.
 */
import { useSyncExternalStore, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { subscribeBusy, isAppBusy, busyLabel } from '@/lib/app-busy';
import {
  BUILD_ID,
  applyUpdate,
  getUpdateState,
  subscribeUpdateState,
} from '@/lib/pwa-update';

export function UpdatePrompt() {
  const updateReady = useSyncExternalStore(
    subscribeUpdateState,
    () => getUpdateState().ready,
  );
  const busy = useSyncExternalStore(subscribeBusy, isAppBusy);
  const [dismissed, setDismissed] = useState(false);
  const [applying, setApplying] = useState(false);

  if (!updateReady || dismissed) return null;

  const holdReason = busy ? busyLabel() : null;

  return (
    <div
      className="fixed z-[70] left-1/2 -translate-x-1/2 bottom-[calc(var(--shell-tabbar-h,0px)+16px)] w-[min(92vw,380px)] rounded-[14px] border border-card-border bg-card p-4 space-y-3"
      style={{ boxShadow: 'var(--gd-shadow)' }}
      role="status"
      data-testid="update-prompt"
    >
      <div className="flex items-start gap-2.5">
        <RefreshCw className="w-4 h-4 mt-0.5 shrink-0" style={{ color: 'var(--gd-bronze)' }} />
        <div className="min-w-0 space-y-0.5">
          <p className="text-[13.5px] font-medium text-foreground">A new version is ready</p>
          <p className="text-[11.5px] font-mono text-muted-foreground truncate">
            build {BUILD_ID}
          </p>
          {holdReason && (
            <p className="text-[11.5px]" style={{ color: 'var(--gd-bronze)' }}>
              {holdReason} — updating is paused so nothing is lost.
            </p>
          )}
        </div>
      </div>
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="text-xs"
          onClick={() => setDismissed(true)}
        >
          Later
        </Button>
        <Button
          size="sm"
          className="text-xs"
          disabled={busy || applying}
          data-testid="update-now"
          onClick={async () => {
            setApplying(true);
            const ok = await applyUpdate();
            if (!ok) setApplying(false); // busy raced in — keep the prompt
          }}
        >
          {applying ? 'Updating…' : 'Update now'}
        </Button>
      </div>
    </div>
  );
}
