# Troubleshooting

## Enable debug logging

Temporarily, without restart: **Settings → Devices & services → Becker
Centronic → Enable debug logging**, or via action:

```yaml
action: logger.set_level
data:
  custom_components.becker_centronic: debug
```

Permanently, in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.becker_centronic: debug
```

With debug logging every sent and received packet is logged, including the
unit ID and channel of your physical remotes.

## A cover stopped responding to Home Assistant

Becker receivers use a rolling code counter ("increment"). If the stick's
counter falls out of the window the receiver accepts (e.g. after restoring a
backup or switching machines without the storage), commands are silently
ignored.

**Fix: press the cover's "Resync" button** (on the cover's device page,
under *Configuration*), or call the `becker_centronic.resync` action on the
cover entity. This repeats STOP three times, which forces the receiver to
re-adopt the stick's current counter. The cover may briefly stop/jerk -
that's expected.

If resync does not help, the pairing itself may be lost - run the pairing
wizard again (delete the cover, **Add cover**).

## Finding the remote ID of a physical remote

1. Enable debug logging (above).
2. Press any button on the remote.
3. Look for a log line like:

```
Received packet: unit_id: 12345, channel: 2, command: halt, ...
```

Enter `12345:2` in the cover's **Remote IDs to track** setting (Configure
cover on the integration page). Multiple IDs are comma separated. Multi
channel remotes broadcasting to all channels are matched automatically.

## Stick not found / cannot connect

- **Local USB**: check the device path exists (`ls /dev/serial/by-id/`).
  After plugging into a different port the `by-id` path stays the same, but
  `/dev/ttyACM*` style paths change. In containers/VMs make sure the USB
  device is passed through.
- **Port busy**: only one program can use the stick. Stop any old
  `centronic-stick.py`, FHEM or previous becker integration still accessing
  it.
- **Network**: check `nc -z <host> 5000` from the Home Assistant machine and
  `systemctl status ser2net` on the remote box. See the
  [remote serial manual](remote-serial.md).

## Covers become unavailable from time to time

That is the integration telling you the connection to the stick dropped; it
reconnects automatically. If it happens often with a network setup, check
Wi-Fi stability of the remote box (prefer Ethernet) and make sure ser2net
uses `kickolduser: true`.

## Pairing does not work

- Pairing mode times out after a few seconds - always put the cover into
  pairing mode (single *clack*) right before pressing Submit.
- Use the wizard's **alternative pairing sequence** options; receivers of
  different generations accept different telegrams.
- Move the stick closer to the cover for pairing (USB extension cable or
  remote serial setup).
- Make sure the unit/channel is not already used by another cover.

## Diagnostics

**Settings → Devices & services → Becker Centronic → ⋮ → Download
diagnostics** produces a file with the connection state, the unit counters
and the last 50 sent/received packets - include it when reporting an
[issue](https://github.com/petrleocompel/ha-becker/issues).

## Migrating from the old becker integration

1. Remove the old `becker:`/`cover: - platform: becker` YAML and the old
   custom component, but **keep** `centronic-stick.db` in the config folder.
2. Restart, install this integration and set it up - it offers to import the
   database counters.
3. Add each cover via **Add cover** with the *"already paired"* checkbox and
   the same unit/channel as in your old YAML (`channel: "2:1"` means unit 2,
   channel 1; a plain `channel: "3"` means unit 1, channel 3).
4. If a cover does not respond, press its **Resync** button once or twice.
