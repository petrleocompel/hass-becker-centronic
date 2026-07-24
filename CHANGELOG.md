# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-24

First release of the new UI-first Becker Centronic integration
(domain `becker_centronic`), a full rewrite of the previous YAML-based
becker component.

### Added

- **UI configuration** - the stick and every cover are set up entirely from
  the Home Assistant UI (config entry + one subentry per cover). No YAML.
- **Guided pairing wizard** with automatic free unit/channel suggestion,
  clack-confirmation step and retry loop offering three known TRAIN sequence
  variants for receivers of different generations.
- **Two connection modes** as first-class options: local USB serial port
  (with port picker preferring stable `/dev/serial/by-id` paths) and remote
  serial over TCP (ser2net on a Raspberry Pi or similar).
- **Robust connection handling**: async I/O, automatic reconnect with capped
  backoff, TCP keepalive, entities become unavailable during outages and
  recover on their own.
- **Protocol pacing**: enforced 0.3 s gap between transmitted packets and
  hold-off after RF receive traffic (root cause fix for lost/garbled
  commands in earlier implementations).
- **Increment counters in Home Assistant storage** instead of a SQLite file,
  with one-time import from an existing `centronic-stick.db` so
  already-paired covers keep working after migration.
- **Cover features**: open/close/stop, position via travel times with
  position slider, intermediate positions (ventilation / sun protection),
  tilt as intermediate positions or blind slat tilt, optional position value
  template, position restore across restarts.
- **Remote tracking**: covers follow commands from paired physical remotes;
  every received packet fires a `becker_centronic_remote_packet_received`
  event and covers with configured remotes offer device triggers.
- **Recovery tools**: per-cover *Resync* button plus `resync`, `unpair`,
  `pair_master` (TRAINMASTER) and `clear_position` (CLEARPOS) actions.
- **Diagnostics** download with connection state, unit counters, cover
  configuration and the last 50 sent/received packets.
- Documentation: pairing guide, remote serial manual (ser2net on Raspberry
  Pi), troubleshooting and migration guide.

[1.0.0]: https://github.com/petrleocompel/hass-becker-centronic/releases/tag/v1.0.0
