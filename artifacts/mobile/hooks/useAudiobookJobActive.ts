/**
 * useAudiobookJobActive
 *
 * Returns true while an audiobook generation job is persisted in AsyncStorage
 * (i.e. while `AUDIOBOOK_JOB_KEY` has a value). Polls every 5 s and
 * re-checks immediately when the app returns to the foreground, so the badge
 * clears as soon as the Studio screen removes the key on completion/cancel.
 */
import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

/** Must match AUDIOBOOK_JOB_KEY in studio.tsx */
const AUDIOBOOK_JOB_KEY = 'orivellum:audiobook_job_v1';

const POLL_MS = 5_000;

export function useAudiobookJobActive(): boolean {
  const [active, setActive] = useState(false);

  const check = useCallback(async () => {
    try {
      const val = await AsyncStorage.getItem(AUDIOBOOK_JOB_KEY);
      setActive(val !== null);
    } catch {
      // AsyncStorage failures are non-fatal; badge simply won't update
    }
  }, []);

  useEffect(() => {
    // Immediate check on mount
    check();
    // Periodic polling
    const interval = setInterval(check, POLL_MS);
    // Re-check when the app comes back to the foreground (e.g. user returns
    // from another app after backgrounding during generation)
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') check();
    });
    return () => {
      clearInterval(interval);
      sub.remove();
    };
  }, [check]);

  return active;
}
