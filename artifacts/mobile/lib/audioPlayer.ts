/**
 * Guarded audio-player creation shared by Studio, read-aloud, and narration.
 *
 * `createAudioPlayer({ uri, headers })` can throw synchronously when the JS
 * and native module versions disagree (seen on iOS as "Calling the
 * 'constructor' function has failed — Received 4 arguments, but 3 was
 * expected") or when a source option is not supported by the native side.
 *
 * Instead of surfacing that exception to the user, this helper falls back to
 * downloading the file with authenticated headers into the cache directory
 * and playing the local copy — which uses the simplest possible source shape.
 */
import { createAudioPlayer, type AudioPlayer } from 'expo-audio';
import { getApiToken } from './token';

/** Build the standard auth headers for audio requests, if a token exists. */
export function audioAuthHeaders(): Record<string, string> | undefined {
  const token = getApiToken();
  return token ? { authorization: `Bearer ${token}` } : undefined;
}

/**
 * Create a player for a remote `uri`, attaching bearer auth headers.
 * If the native constructor rejects the source, download the audio to a
 * local cache file (with the same auth headers) and play that instead.
 */
export async function createPlayerSafe(uri: string): Promise<AudioPlayer> {
  const headers = audioAuthHeaders();
  try {
    return createAudioPlayer({ uri, headers });
  } catch {
    // Native bridge rejected the source — fall back to a local file.
    const FileSystem = await import('expo-file-system/legacy');
    const ext = uri.includes('.wav') ? 'wav' : 'mp3';
    const dest =
      `${FileSystem.cacheDirectory}tts-${Date.now()}-` +
      `${Math.random().toString(36).slice(2)}.${ext}`;
    const dl = await FileSystem.downloadAsync(
      uri,
      dest,
      headers ? { headers } : undefined,
    );
    return createAudioPlayer({ uri: dl.uri });
  }
}
