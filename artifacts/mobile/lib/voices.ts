/**
 * Shared voice catalog for TTS features.
 * Mirrors _VOICE_CATALOG in studio.py; kept in one place so both Studio and
 * library Read Aloud use the same list without duplicating the data.
 */

export interface VoiceEntry {
  id: string;
  name: string;
  accent?: 'american' | 'british' | 'custom';
  gender?: 'feminine' | 'masculine';
  tags?: string[];
}

export const VOICES: VoiceEntry[] = [
  // American Female
  { id: 'af_heart',   name: 'Heart',   accent: 'american', gender: 'feminine',  tags: ['literary fiction', 'memoir', 'spiritual'] },
  { id: 'af_bella',   name: 'Bella',   accent: 'american', gender: 'feminine',  tags: ['thriller', 'young adult', 'adventure'] },
  { id: 'af_nova',    name: 'Nova',    accent: 'american', gender: 'feminine',  tags: ['non-fiction', 'documentary', 'academic'] },
  { id: 'af_alloy',   name: 'Alloy',   accent: 'american', gender: 'feminine',  tags: ['academic', 'news', 'instructional'] },
  { id: 'af_sarah',   name: 'Sarah',   accent: 'american', gender: 'feminine',  tags: ['literary fiction', 'memoir', 'spiritual'] },
  { id: 'af_sky',     name: 'Sky',     accent: 'american', gender: 'feminine',  tags: ['children', 'young adult', 'fantasy'] },
  { id: 'af_jessica', name: 'Jessica', accent: 'american', gender: 'feminine',  tags: ['mystery', 'literary fiction', 'thriller'] },
  { id: 'af_kore',    name: 'Kore',    accent: 'american', gender: 'feminine',  tags: ['epic', 'literary fiction', 'mythology'] },
  { id: 'af_nicole',  name: 'Nicole',  accent: 'american', gender: 'feminine',  tags: ['memoir', 'self-help', 'romance'] },
  { id: 'af_aoede',   name: 'Aoede',   accent: 'american', gender: 'feminine',  tags: ['literary fiction', 'poetry', 'spiritual'] },
  { id: 'af_river',   name: 'River',   accent: 'american', gender: 'feminine',  tags: ['meditation', 'spiritual', 'nature'] },
  // American Male
  { id: 'am_adam',    name: 'Adam',    accent: 'american', gender: 'masculine', tags: ['epic', 'historical', 'thriller'] },
  { id: 'am_echo',    name: 'Echo',    accent: 'american', gender: 'masculine', tags: ['non-fiction', 'documentary', 'news'] },
  { id: 'am_eric',    name: 'Eric',    accent: 'american', gender: 'masculine', tags: ['memoir', 'literary fiction', 'thriller'] },
  { id: 'am_fenrir',  name: 'Fenrir',  accent: 'american', gender: 'masculine', tags: ['epic', 'mythology', 'horror'] },
  { id: 'am_liam',    name: 'Liam',    accent: 'american', gender: 'masculine', tags: ['young adult', 'adventure', 'sci-fi'] },
  { id: 'am_michael', name: 'Michael', accent: 'american', gender: 'masculine', tags: ['non-fiction', 'historical', 'documentary'] },
  { id: 'am_onyx',    name: 'Onyx',    accent: 'american', gender: 'masculine', tags: ['epic', 'thriller', 'historical'] },
  { id: 'am_puck',    name: 'Puck',    accent: 'american', gender: 'masculine', tags: ['young adult', 'adventure', 'comedy', 'fantasy'] },
  { id: 'am_santa',   name: 'Santa',   accent: 'american', gender: 'masculine', tags: ['children', 'family', 'holiday', 'feel-good'] },
  // British Female
  { id: 'bf_emma',     name: 'Emma',     accent: 'british', gender: 'feminine',  tags: ['literary fiction', 'historical', 'mystery'] },
  { id: 'bf_alice',    name: 'Alice',    accent: 'british', gender: 'feminine',  tags: ['academic', 'documentary', 'historical'] },
  { id: 'bf_isabella', name: 'Isabella', accent: 'british', gender: 'feminine',  tags: ['literary fiction', 'romance', 'historical'] },
  { id: 'bf_lily',     name: 'Lily',     accent: 'british', gender: 'feminine',  tags: ['children', 'young adult', 'romance'] },
  // British Male
  { id: 'bm_george', name: 'George', accent: 'british', gender: 'masculine', tags: ['historical', 'literary fiction', 'epic'] },
  { id: 'bm_daniel', name: 'Daniel', accent: 'british', gender: 'masculine', tags: ['literary fiction', 'memoir', 'mystery'] },
  { id: 'bm_fable',  name: 'Fable',  accent: 'british', gender: 'masculine', tags: ['epic', 'mythology', 'fantasy'] },
  { id: 'bm_lewis',  name: 'Lewis',  accent: 'british', gender: 'masculine', tags: ['non-fiction', 'academic', 'historical'] },
];
