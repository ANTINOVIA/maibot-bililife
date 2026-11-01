CREATE TABLE IF NOT EXISTS chat_requests(
 id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, mode TEXT NOT NULL, query TEXT NOT NULL,
 share_after INTEGER NOT NULL, root_task_id TEXT NOT NULL, state TEXT NOT NULL,
 result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
 notification_state TEXT NOT NULL DEFAULT 'pending', notification_result TEXT NOT NULL DEFAULT '{}',
 created REAL NOT NULL, updated REAL NOT NULL, deadline REAL NOT NULL,
 FOREIGN KEY(root_task_id) REFERENCES tasks(id));
CREATE INDEX IF NOT EXISTS chat_requests_stream_time ON chat_requests(stream_id,created);
