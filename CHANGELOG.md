# Changelog

## Unreleased

### Added
- **Live FPS:** the server measures the frames per second each camera delivers (2-second window) and exposes them at `GET /streams/fps`. The Android app shows the number next to the camera name in the multi-view and the single live view, updated every second, for every signed-in user.
- **Paged Reboots & Dropouts:** `GET /cameras/{id}/events` accepts `limit` and `offset` and returns `has_more` and `next_offset`. The app loads 50 events at a time with a **Load more** button.
- **Owner accounts:** accounts listed in `SURVCAM_CONTROL_USERS` can now do everything an admin can (create users, add cameras, change camera status, start/stop recording, clean up recordings) in addition to camera control (firmware, OTA, reboot, rollback). They no longer need the admin role.
- **Snapshot retention:** motion snapshots are deleted after 30 days by their own daily job (`snapshot_cleanup.py`), independent of the recordings.
- **Motion event retention:** motion events older than 30 days are deleted daily.
- Tests for FPS, event paging, permissions and the retention jobs.

### Changed
- Reboots & Dropouts is computed inside SQLite (window functions) instead of in Python, with a new `(camera_id, recorded_at)` index on the telemetry table. About 5 times faster on a 14-day, 12-camera database, with identical results.
- Deleting recordings (age limit or low disk space) no longer deletes motion snapshots.
- SQLite connections wait up to 30 seconds for a lock instead of 5.
- Heartbeat contents are no longer printed to the log (they contain the camera password).

### Fixed
- **Heartbeats stopping after "database is locked":** an error while handling one MQTT message used to kill the MQTT thread, so no heartbeat was processed again until a restart. Message handling now catches errors and keeps running.
- **Heartbeat history was never cleaned up.** The 7-day cleanup existed but was never called, so the table grew without limit. It now runs daily, deleting in small batches. See "Upgrading an existing install" in the README for rebuilding a table that is already huge.
- Tests that depended on the old "recordings delete snapshots" behaviour were updated.

### Upgrading
See [Upgrading an existing install](README.md#upgrading-an-existing-install).
