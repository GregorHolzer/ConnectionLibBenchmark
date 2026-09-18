# Pi-side remote power control

The bench host (the machine with the boards and the PPK2) is normally off. A Raspberry Pi on the
same network stays on and acts as a jump host and power switch:

```
VPN → raspberrypi → start_desktop → ssh desktop → work
```

Install by copying all four files into `~/.local/bin` on the Pi:

```bash
scp tools/pi/* raspberrypi:~/.local/bin/
ssh raspberrypi 'chmod +x ~/.local/bin/{start_desktop,stop_desktop,desktop_status}'
```

`desktop_lib.py` is imported by the other three, so it has to sit beside them.

## Why they are shaped the way they are

**Reachability is TCP/22, never ping.** The bench host drops ICMP, so a ping-based check reports
failure on a machine that is up and perfectly usable.

**Addressing is a self-healing fallback chain**, not a name. mDNS resolution of `desktop.local`
from the Pi proved *intermittent* — the same `getent hosts` succeeded and then failed a minute
later — and an intermittently-resolvable name is worse than none for a remote power switch,
because it fails when you are away. So `desktop_lib` tries the hostname, then the last address
that actually worked (cached in `~/.cache/desktop_ip`), then a built-in default, and refreshes the
cache on every success. Waking itself needs no address at all: the magic packet is addressed by
MAC to the broadcast, so a changed DHCP lease cannot stop the machine coming up.

**An unknown campaign state is not "idle".** `stop_desktop` refuses to power off both when a
campaign is running *and* when it could not determine whether one is. A full campaign is ten hours
of bench time; powering off on a failed check is the one mistake these scripts exist to prevent.

## Host-side requirements

On the bench host, all one-time:

- Wake-on-LAN on the NIC, made persistent through NetworkManager
  (`802-3-ethernet.wake-on-lan magic`) **and** a systemd one-shot running `ethtool -s <iface> wol g`.
  Both, because the r8169 driver is known to drop the flag across driver reloads and resume.
- **In the BIOS:** enable *Wake on LAN* / *Power On By PCI-E* and **disable ErP/EuP**. ErP cuts
  standby power to the NIC and is the usual reason a machine wakes from suspend but not from a
  full power-off. This cannot be set from the OS.
- The Pi's public key in `~/.ssh/authorized_keys`, and the host's key in the Pi's `known_hosts`,
  or the scripts hang on a prompt no one is there to answer.

Check the first of these with `ethtool <iface> | grep -i wake` (needs root); you want `Wake-on: g`.
