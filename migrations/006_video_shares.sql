CREATE TABLE IF NOT EXISTS video_shares(
 id TEXT PRIMARY KEY, video_id TEXT NOT NULL, stream_id TEXT NOT NULL, format TEXT NOT NULL,
 state TEXT NOT NULL, message_id TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(video_id,stream_id), FOREIGN KEY(video_id) REFERENCES videos(id));
CREATE INDEX IF NOT EXISTS video_shares_stream_time ON video_shares(stream_id,created);
