CREATE TABLE IF NOT EXISTS adaptive_decisions(source_key TEXT PRIMARY KEY, day TEXT NOT NULL, video_id TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS draft_mentions(draft_id TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL, FOREIGN KEY(draft_id) REFERENCES drafts(id));
CREATE TABLE IF NOT EXISTS draft_origins(draft_id TEXT PRIMARY KEY, origin TEXT NOT NULL, FOREIGN KEY(draft_id) REFERENCES drafts(id));
