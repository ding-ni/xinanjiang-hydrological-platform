BEGIN;
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stations (
    project_id TEXT NOT NULL REFERENCES projects(id), station_id TEXT NOT NULL,
    name TEXT NOT NULL, kind TEXT NOT NULL, lon REAL, lat REAL, metadata TEXT NOT NULL,
    PRIMARY KEY(project_id, station_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS station_aliases (
    project_id TEXT NOT NULL, namespace TEXT NOT NULL, alias TEXT NOT NULL, station_id TEXT NOT NULL,
    PRIMARY KEY(project_id,namespace,alias),
    FOREIGN KEY(project_id,station_id) REFERENCES stations(project_id,station_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS source_files (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), category TEXT NOT NULL,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, records INTEGER NOT NULL, imported_at TEXT NOT NULL,
    UNIQUE(project_id,relative_path)
);
CREATE TABLE IF NOT EXISTS observations (
    source_id TEXT NOT NULL REFERENCES source_files(id), project_id TEXT NOT NULL, station_id TEXT NOT NULL,
    variable TEXT NOT NULL CHECK(variable IN ('rain','level','flow')), time_hour INTEGER NOT NULL, value REAL,
    PRIMARY KEY(project_id,station_id,variable,time_hour,source_id),
    FOREIGN KEY(project_id,station_id) REFERENCES stations(project_id,station_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS observations_source ON observations(source_id);
CREATE TABLE IF NOT EXISTS observation_coverage (
    source_id TEXT NOT NULL REFERENCES source_files(id), project_id TEXT NOT NULL, station_id TEXT NOT NULL,
    variable TEXT NOT NULL, records INTEGER NOT NULL, valid INTEGER NOT NULL, zeros INTEGER NOT NULL,
    start_hour INTEGER NOT NULL, end_hour INTEGER NOT NULL,
    PRIMARY KEY(source_id,project_id,station_id,variable),
    FOREIGN KEY(project_id,station_id) REFERENCES stations(project_id,station_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS flood_events (
    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
    start TEXT NOT NULL, end TEXT NOT NULL, flood_code TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(project_id,id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS task_index (
    queue TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL, run_type TEXT NOT NULL,
    created_at TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(queue,id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS schemes (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, source_task_id TEXT NOT NULL,
    request TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_metadata (
    state_id TEXT PRIMARY KEY, name TEXT NOT NULL,
    favorite INTEGER NOT NULL CHECK(favorite IN (0,1)),
    revision INTEGER NOT NULL CHECK(revision >= 1), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS result_metadata (
    task_id TEXT PRIMARY KEY, name TEXT NOT NULL, collection TEXT NOT NULL,
    notes TEXT NOT NULL, favorite INTEGER NOT NULL CHECK(favorite IN (0,1)),
    revision INTEGER NOT NULL CHECK(revision >= 1), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_drafts (
    key TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
COMMIT;
