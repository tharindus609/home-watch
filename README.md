# home-watch

Reads room temperature and humidity from a DHT22/AM2302 sensor on a Raspberry Pi
and exposes them as Prometheus metrics.

Readings come from the Linux kernel's `dht11` driver (which also drives the
DHT22/AM2302) rather than a userspace GPIO library. The kernel does the
timing-critical work, so there is no busy-waiting, no root requirement, and the
only Python dependency is `prometheus-client`.

## Wiring

| Sensor pin | Raspberry Pi             |
| ---------- | ------------------------ |
| VCC        | 3V3 (physical pin 1)     |
| DATA       | GPIO12 (physical pin 32) |
| GND        | GND (physical pin 6)     |

A 10kΩ pull-up resistor between VCC and DATA is required if your breakout board
does not already have one.

## Setup

**1. Enable the kernel driver.** Add this to `/boot/firmware/config.txt`:

```
dtoverlay=dht11,gpiopin=12
```

`gpiopin` is **BCM numbering**, not the physical pin number. Reboot, then check
the sensor was detected:

```bash
grep dht11 /sys/bus/iio/devices/iio:device*/name
```

That should print something like `/sys/bus/iio/devices/iio:device0/name:dht11@18`.
The `@18` is the device tree unit address and varies with the GPIO you chose;
the script matches on the `dht11` prefix, so any suffix is fine. If the command
prints nothing, see [Troubleshooting](#troubleshooting).

**2. Install [uv](https://docs.astral.sh/uv/).** Python itself is handled by uv;
there is no virtualenv to create or activate.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Running

```bash
git clone https://github.com/<you>/home-watch.git ~/home-watch
cd ~/home-watch

# Reads pyproject.toml + uv.lock, creates .venv and fetches deps on first run.
uv run --frozen home-watch.py
```

Metrics are then served on <http://raspberrypi.local:9101/metrics>:

```
home_watch_temperature_celsius 23.4
home_watch_humidity_percent 51.2
home_watch_sensor_reads_total{result="success"} 3.0
home_watch_sensor_reads_total{result="failure"} 1.0
home_watch_last_success_timestamp_seconds 1.786752125e+09
```

Useful uv commands:

| Command | What it does |
| ------- | ------------ |
| `uv run --frozen home-watch.py` | Run using the exact locked versions |
| `uv sync --frozen` | Pre-install deps without starting the script |
| `uv lock --upgrade` | Refresh `uv.lock` to newer versions |
| `uv add <package>` | Add a dependency and update the lock |

## Configuration

All settings are environment variables, so nothing needs editing in the script.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `HOME_WATCH_METRICS_PORT` | `9101` | Port the metrics server listens on |
| `HOME_WATCH_METRICS_ADDR` | `0.0.0.0` | Bind address; use `127.0.0.1` to keep it local |
| `HOME_WATCH_INTERVAL` | `15` | Seconds between readings |
| `HOME_WATCH_MAX_ATTEMPTS` | `3` | Retries per reading before it counts as a failure |
| `HOME_WATCH_BACKEND` | `auto` | `iio` (kernel driver), `gpio` (userspace), or `auto` |
| `HOME_WATCH_PIN` | `12` | BCM pin — only used by the `gpio` backend |
| `HOME_WATCH_LOG_LEVEL` | `INFO` | Python log level |

`auto` uses the kernel driver when the overlay is loaded and falls back to the
userspace GPIO backend otherwise. Set `HOME_WATCH_BACKEND=iio` to make a missing
overlay a hard error instead of a silent fallback.

## Running as a service

`start.sh` is fine for a quick manual start, but it will not come back after a
crash or a reboot. Use the bundled systemd unit instead. It is a *user* unit, so
it needs no root and no editing — `%h` expands to your home directory:

```bash
mkdir -p ~/.config/systemd/user
cp home-watch.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now home-watch

# start at boot without having to log in first
sudo loginctl enable-linger "$USER"
```

Do not skip the `enable-linger` line: without it the user manager only runs
while you are logged in, so the exporter stops when your session ends.

```bash
systemctl --user status home-watch     # health
journalctl --user -u home-watch -f     # follow the logs
```

The unit assumes the repo is at `~/home-watch` and uv at `~/.local/bin/uv`,
which is where uv's install script puts it. If the service fails to start with
status 203/EXEC, `which uv` will tell you where yours actually is.

If you fall back to the userspace `gpio` backend it needs root, which a user
unit cannot provide — install it into `/etc/systemd/system/` as a system unit
instead, and spell out absolute paths, since systemd resolves `%h` to `/root` in
system units no matter what `User=` says.

## Scraping with Prometheus

```yaml
scrape_configs:
  - job_name: home-watch
    static_configs:
      - targets: ["raspberrypi.local:9101"]
```

The gauges keep their last known value when a read fails, so alert on staleness
rather than on the gauges disappearing:

```yaml
- alert: HomeWatchSensorStale
  expr: time() - home_watch_last_success_timestamp_seconds > 120
  for: 5m
  annotations:
    summary: "home-watch has not read the sensor successfully in 2 minutes"
```

## Troubleshooting

**No `dht11` device in `/sys/bus/iio/devices/`.** Confirm the overlay line is in
`/boot/firmware/config.txt` (not the older `/boot/config.txt`) and that you have
rebooted. `dmesg | grep dht11` shows whether the driver loaded, and
`sudo vcdbg log msg 2>&1 | grep -i dht` shows overlay load failures.

**Every read fails.** Occasional `-EIO` (checksum) and `-ETIMEDOUT` errors are
normal for DHT22 sensors; the script retries and counts them in
`home_watch_sensor_reads_total{result="failure"}`. Sustained failure usually
means wiring, a missing pull-up resistor, or the wrong `gpiopin` in the overlay.

**Permission denied reading sysfs.** The IIO attributes are normally
world-readable. If yours are not, run the service as root or add a udev rule
granting your user access to `/sys/bus/iio/devices/iio:device*`.

**Temperature reads high.** Keep the sensor away from the Pi's own heat and
avoid very short `HOME_WATCH_INTERVAL` values, which cause sensor self-heating.
The kernel driver caches each reading for 2s, so polling faster than that just
returns the same sample.

## The userspace GPIO fallback

If the kernel overlay is not an option, the script can bit-bang the GPIO line
from Python instead. That stack is not installed by default — it pulls in 18
extra packages and is markedly less reliable, since userspace has to meet the
sensor's microsecond timing:

```bash
uv run --extra gpio home-watch.py     # needs root for /dev/mem
```

Two notes on that path, both of which cost time to rediscover:

- `adafruit-blinka` imports `RPi.GPIO` but does not declare it as a dependency,
  so it fails at runtime with `ModuleNotFoundError: No module named 'RPi'`.
- Blinka's suggested fix, `pip install RPi.GPIO`, does not work on Python 3.13:
  RPi.GPIO 0.7.1 calls `PyEval_InitThreads()`, which was removed in that
  release, so the C extension will not compile. The `gpio` extra therefore
  depends on `rpi-lgpio`, a drop-in replacement that provides the same module.

The original `Adafruit_DHT` library this project started with is no longer
supported at all: it has been archived by Adafruit, and its `setup.py` reads
`/proc/cpuinfo` at build time and aborts on anything it does not recognise —
including the Pi 4's BCM2711 — so neither uv nor pip can install it.
