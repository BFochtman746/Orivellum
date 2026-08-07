/**
 * useAudiobookJobActive
 *
 * Returns progress data while an audiobook generation job is persisted in
 * AsyncStorage (i.e. while `AUDIOBOOK_JOB_KEY` has a value). Polls every 5 s
 * and re-checks immediately when the app returns to the foreground, so the
 * banner clears as soon as the Studio screen removes the key on completion/cancel.
 *
 * The AsyncStorage value is a JSON object written by AudiobookPanel in studio.tsx:
 *   { job_id, work_title, chapter_idx, total_chapters }
 * Earlier saves (before progress was added) may only have job_id + work_title;
 * those parse safely — chapter fields default to 0.
 *
 * Completion detection
 * --------------------
 * When generation finishes successfully, studio.tsx writes AUDIOBOOK_DONE_KEY
 * before removing the job key.  This hook reads that flag and sets
 * `justCompleted: true` so the layout can show a "ready" banner.  Call
 * `dismissReady()` to clear the flag and hide the banner; the layout also
 * auto-dismisses after ~8 s of foreground time.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

/** Must match AUDIOBOOK_JOB_KEY in studio.tsx */
const AUDIOBOOK_JOB_KEY  = 'orivellum:audiobook_job_v1';
/** Written by studio.tsx on successful completion; cleared by dismissReady(). */
const AUDIOBOOK_DONE_KEY = 'orivellum:audiobook_done_v1';

const POLL_MS = 5_000;

export interface AudiobookJobProgress {
  /** True while a job is persisted in AsyncStorage. */
  active: boolean;
  /** Number of chapters completed so far (0-based count, not index). */
  chapterIdx: number;
  /** Total chapters in the audiobook; 0 while not yet reported by the server. */
  totalChapters: number;
  /** Work title for the banner label; empty string until the job is written. */
  workTitle: string;
  /**
   * Briefly true (~8 s of foreground time) after successful completion.
   * False on cancel, error, or after dismissReady() is called.
   */
  justCompleted: boolean;
}

const IDLE: AudiobookJobProgress = {
  active:        false,
  chapterIdx:    0,
  totalChapters: 0,
  workTitle:     '',
  justCompleted: false,
};

export function useAudiobookJobActive(): AudiobookJobProgress & {
  dismissReady: () => void;
} {
  const [progress, setProgress] = useState<AudiobookJobProgress>(IDLE);

  // dismissReady: clears the persistent done flag and hides the ready banner.
  const dismissReady = useCallback(() => {
    AsyncStorage.removeItem(AUDIOBOOK_DONE_KEY).catch(() => {});
    setProgress(prev => ({ ...prev, justCompleted: false }));
  }, []);

  const check = useCallback(async () => {
    try {
      const raw = await AsyncStorage.getItem(AUDIOBOOK_JOB_KEY);

      if (raw === null) {
        // No active job — check whether a successful completion is pending.
        const done = await AsyncStorage.getItem(AUDIOBOOK_DONE_KEY).catch(() => null);
        setProgress(prev => {
          // If the done key is present, set justCompleted.
          // If justCompleted is already true (set earlier), preserve it so the
          // layout's 8 s timer can dismiss it rather than the next poll clearing it.
          const justCompleted = done !== null || prev.justCompleted;
          return { ...IDLE, justCompleted };
        });
        return;
      }

      // Active job — parse progress and clear any stale completion flag.
      try {
        const parsed: {
          job_id?: string;
          work_title?: string;
          chapter_idx?: number;
          total_chapters?: number;
        } = JSON.parse(raw);
        setProgress({
          active:        true,
          chapterIdx:    parsed.chapter_idx    ?? 0,
          totalChapters: parsed.total_chapters ?? 0,
          workTitle:     parsed.work_title     ?? '',
          justCompleted: false,
        });
      } catch {
        // Malformed JSON — still active (key exists), just no progress info.
        setProgress({ active: true, chapterIdx: 0, totalChapters: 0, workTitle: '', justCompleted: false });
      }
    } catch {
      // AsyncStorage failures are non-fatal; progress simply won't update.
    }
  }, []);

  useEffect(() => {
    // Immediate check on mount
    check();
    // Periodic polling
    const interval = setInterval(check, POLL_MS);
    // Re-check when the app comes back to the foreground
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') check();
    });
    return () => {
      clearInterval(interval);
      sub.remove();
    };
  }, [check]);

  return { ...progress, dismissReady };
}
