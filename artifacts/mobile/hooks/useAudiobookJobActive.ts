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
 */
import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

/** Must match AUDIOBOOK_JOB_KEY in studio.tsx */
const AUDIOBOOK_JOB_KEY = 'orivellum:audiobook_job_v1';

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
}

const IDLE: AudiobookJobProgress = {
  active: false,
  chapterIdx: 0,
  totalChapters: 0,
  workTitle: '',
};

export function useAudiobookJobActive(): AudiobookJobProgress {
  const [progress, setProgress] = useState<AudiobookJobProgress>(IDLE);

  const check = useCallback(async () => {
    try {
      const raw = await AsyncStorage.getItem(AUDIOBOOK_JOB_KEY);
      if (raw === null) {
        setProgress(IDLE);
        return;
      }
      try {
        const parsed: {
          job_id?: string;
          work_title?: string;
          chapter_idx?: number;
          total_chapters?: number;
        } = JSON.parse(raw);
        setProgress({
          active: true,
          chapterIdx: parsed.chapter_idx ?? 0,
          totalChapters: parsed.total_chapters ?? 0,
          workTitle: parsed.work_title ?? '',
        });
      } catch {
        // Malformed JSON — still active (key exists), just no progress info
        setProgress({ active: true, chapterIdx: 0, totalChapters: 0, workTitle: '' });
      }
    } catch {
      // AsyncStorage failures are non-fatal; progress simply won't update
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

  return progress;
}
