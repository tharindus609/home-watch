# home-watch

Reads room temperature and humidity from a DHT22/AM2302 sensor on a Raspberry Pi
and exposes them as Prometheus metrics.

## Wiring

| Sensor pin | Raspberry Pi        |
| ---------- | ------------------- |
| VCC        | 3V3 (physical pin 1) |
| DATA       | GPIO12 (physical pin 32) |
| GND        | GND (physical pin 6) |

A 10kΩ pull-up resistor between VCC and DATA is required if your breakout board
does not already have one. To use a different GPIO, set `HOME_WATCH_PIN` — the
value is **BCM numbering**, not the physical pin number.

## Requirements

- Raspberry Pi running Raspberry Pi OS (tested on a Pi 4)
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- `libgpiod` for the sensor driver: `sudo apt install -y libgpiod2 python3-libgpiod`

Python itself is handled by uv; there is no virtualenv to create or activate.

## Running

```bash
git clone https://github.com/<you>/home-watch.git ~/src/home-watch
cd ~/src/home-watch

# Reads pyproject.toml + uv.lock, creates .venv and fetches deps on first run.
sudo "$(which uv)" run --frozen home-watch.py
```

`sudo` is needed because bit-banging the GPIO line requires access to `/dev/mem`,
and `$(which uv)` is spelled out because `sudo` resets `PATH`.

Metrics are then served on <http://raspberrypi.local:9101/metrics>:

```
home_watch_temperature_celsius 22.4
home_watch_humidity_percent 51.2
home_watch_sensor_reads_total{result="success"} 3.0
home_watch_sensor_reads_total{result="failure"} 1.0
home_watch_last_success_timestamp_seconds 1.786744495e+09
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
| `HOME_WATCH_PIN` | `12` | Data GPIO, BCM numbering |
| `HOME_WATCH_METRICS_PORT` | `9101` | Port the metrics server listens on |
| `HOME_WATCH_METRICS_ADDR` | `0.0.0.0` | Bind address; use `127.0.0.1` to keep it local |
| `HOME_WATCH_INTERVAL` | `15` | Seconds between readings |
| `HOME_WATCH_MAX_ATTEMPTS` | `3` | Retries per reading before it counts as a failure |
| `HOME_WATCH_LOG_LEVEL` | `INFO` | Python log level |

## Running as a service

`start.sh` is fine for a quick manual start, but it will not come back after a
crash or a reboot. Use the bundled systemd unit instead:

```bash
sudo cp home-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-watch
```

Adjust `WorkingDirectory` and the `ExecStart` path to uv in the unit file if you
cloned somewhere other than `/home/pi/src/home-watch` or installed uv for a user
rather than system-wide (`which uv` will tell you).

```bash
systemctl status home-watch     # health
journalctl -u home-watch -f     # follow the logs
```

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

**Every read fails.** Occasional checksum failures are normal for DHT22 sensors;
sustained failure usually means wiring, a missing pull-up resistor, or the wrong
`HOME_WATCH_PIN`. Check `home_watch_sensor_reads_total{result="failure"}`.

**`Permission denied` on GPIO.** Run under `sudo` / as root — see above.

**Temperature reads high.** Keep the sensor away from the Pi's own heat and
avoid very short `HOME_WATCH_INTERVAL` values, which cause sensor self-heating.

## Notes on the sensor driver

This uses `adafruit-circuitpython-dht`. The older `Adafruit_DHT` library that
this project originally depended on has been archived by Adafruit and cannot be
installed by uv (or pip) at all: its `setup.py` reads `/proc/cpuinfo` at build
time and aborts on anything it does not recognise, including the Pi 4's BCM2711.
The script still falls back to `Adafruit_DHT` automatically if it happens to be
importable on the host, but it is not a managed dependency.
