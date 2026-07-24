# Remote serial setup - USB stick far away from Home Assistant

RF range of the Centronic stick is limited. If your Home Assistant server
sits in the basement but the covers are two floors up, plug the stick into a
small Linux box (Raspberry Pi, old thin client, ...) near the covers and
share its serial port over the network. The integration connects to it over
TCP and reconnects automatically if the network or the box hiccups.

```
Home Assistant  ── LAN/TCP ──  Raspberry Pi ── USB ── Centronic stick  ~~RF~~  covers
```

## 1. Plug in the stick and find its device path

On the Pi:

```bash
ls -l /dev/serial/by-id/
```

You should see something like:

```
usb-BECKER-ANTRIEBE_GmbH_CDC_RS232_v125_Centronic-if00 -> ../../ttyACM0
```

Always use the `/dev/serial/by-id/...` path - it stays stable across reboots
and USB port changes, unlike `/dev/ttyACM0`.

If reception is poor, put the stick on a short USB extension cable, away
from the Pi and other electronics.

## 2. Install and configure ser2net

```bash
sudo apt update && sudo apt install ser2net
```

### Modern ser2net (v4, YAML config)

Edit `/etc/ser2net.yaml` and add:

```yaml
connection: &becker
  accepter: tcp,5000
  enable: on
  options:
    kickolduser: true
  connector: serialdev,
    /dev/serial/by-id/usb-BECKER-ANTRIEBE_GmbH_CDC_RS232_v125_Centronic-if00,
    115200n81,local
```

### Legacy ser2net (v3, /etc/ser2net.conf)

```
5000:raw:0:/dev/serial/by-id/usb-BECKER-ANTRIEBE_GmbH_CDC_RS232_v125_Centronic-if00:115200 8DATABITS NONE 1STOPBIT
```

### Enable and start

```bash
sudo systemctl enable --now ser2net
sudo systemctl status ser2net
```

`kickolduser: true` makes sure a stale half-open connection (e.g. after a
Home Assistant restart) is replaced instead of blocking the port.

## 3. Point the integration at the Pi

In the integration setup choose **Remote over network**, enter the Pi's
hostname or IP and port `5000`. That's all - pairing and control work exactly
as with a local stick.

Give the Pi a **static IP or DHCP reservation** (or use its mDNS hostname,
e.g. `shutterpi.local`) so the address never changes.

## Alternatives to ser2net

**socat** - quick test without installing ser2net:

```bash
socat TCP-LISTEN:5000,reuseaddr,fork \
  FILE:/dev/serial/by-id/usb-BECKER-ANTRIEBE_GmbH_CDC_RS232_v125_Centronic-if00,b115200,raw
```

**usbip** - shares the USB device itself so it appears as a local port on
the Home Assistant machine. Works, but is more fragile across reboots and
kernel updates; ser2net is the recommended way.

## Behavior during outages

- If the Pi reboots, Wi-Fi drops or ser2net restarts, the cover entities
  become **unavailable** and the integration reconnects automatically with
  increasing backoff (up to once a minute). No restart of Home Assistant is
  needed.
- Commands sent while briefly disconnected are queued and transmitted right
  after reconnecting.

## Security note

The serial protocol has **no authentication** - anyone who can reach the TCP
port can move your covers. Keep the port restricted to your LAN (don't
forward it on your router) and consider a firewall rule on the Pi:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 5000 proto tcp
```

(Replace with your Home Assistant server's subnet or address.)
