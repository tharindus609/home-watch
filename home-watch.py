"""Read a DHT22/AM2302 sensor and expose the readings as Prometheus metrics."""

import logging
import os
import signal
import threading
import time

from prometheus_client import Counter, Gauge, start_http_server

log = logging.getLogger("home-watch")


def _env_int(name, default):
    try:
        return int(os.environ[name])
    except KeyError:
        return default
    except ValueError:
        log.warning("%s is not an integer, falling back to %s", name, default)
        return default


PIN = _env_int("HOME_WATCH_PIN", 12)  # BCM numbering: GPIO12 == physical pin 32
METRICS_PORT = _env_int("HOME_WATCH_METRICS_PORT", 9101)
METRICS_ADDR = os.environ.get("HOME_WATCH_METRICS_ADDR", "0.0.0.0")
INTERVAL = _env_int("HOME_WATCH_INTERVAL", 15)
# The DHT22 datasheet requires at least 2s between reads.
RETRY_DELAY = 2.1
MAX_ATTEMPTS = _env_int("HOME_WATCH_MAX_ATTEMPTS", 3)

temperature_gauge = Gauge(
    "home_watch_temperature_celsius", "Room temperature in degrees celsius"
)
humidity_gauge = Gauge(
    "home_watch_humidity_percent", "Relative humidity as a percentage"
)
reads_total = Counter(
    "home_watch_sensor_reads_total", "Sensor read attempts by outcome", ["result"]
)
last_success = Gauge(
    "home_watch_last_success_timestamp_seconds",
    "Unix timestamp of the last successful sensor read",
)

# Pre-create the label sets so both series exist from the first scrape onwards.
reads_total.labels(result="success")
reads_total.labels(result="failure")


class SensorError(Exception):
    """Raised when a single read attempt did not produce a valid measurement."""


def make_reader(pin):
    """Return a callable that performs one read and yields (humidity, temperature).

    Prefers the maintained CircuitPython driver and falls back to the legacy
    (deprecated) Adafruit_DHT library if that is what the host has installed.
    """
    try:
        import adafruit_dht
        import board
    except ImportError:
        pass
    else:
        # use_pulseio=False uses the bitbang driver, which is the path that works
        # on current Raspberry Pi OS releases without the pulseio kernel bits.
        device = adafruit_dht.DHT22(getattr(board, "D{0}".format(pin)), use_pulseio=False)
        log.info("using adafruit_dht (CircuitPython) on GPIO%s", pin)

        def read():
            try:
                # Read humidity first: the driver caches one full sensor frame, so
                # the second attribute access does not trigger another bus read.
                humidity = device.humidity
                temperature = device.temperature
            except RuntimeError as exc:  # checksum/timing failures are routine
                raise SensorError(str(exc))
            if humidity is None or temperature is None:
                raise SensorError("driver returned no measurement")
            return humidity, temperature

        return read

    import Adafruit_DHT

    log.info("using legacy Adafruit_DHT on GPIO%s", pin)

    def read():
        # read() rather than read_retry(): retries are handled below so that a
        # bad sensor cannot block the loop for ~30s at a time.
        humidity, temperature = Adafruit_DHT.read(Adafruit_DHT.AM2302, pin)
        if humidity is None or temperature is None:
            raise SensorError("driver returned no measurement")
        return humidity, temperature

    return read


def read_sensor(read, stop):
    """Read the sensor with bounded retries and update the metrics."""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            humidity, temperature = read()
        except SensorError as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS and not stop.is_set():
                stop.wait(RETRY_DELAY)
            continue

        temperature_gauge.set(temperature)
        humidity_gauge.set(humidity)
        last_success.set(time.time())
        reads_total.labels(result="success").inc()
        log.info("humidity: %.1f%%, temperature: %.1fC", humidity, temperature)
        return

    reads_total.labels(result="failure").inc()
    log.warning("failed to read sensor after %s attempts: %s", MAX_ATTEMPTS, last_error)


def main():
    logging.basicConfig(
        level=os.environ.get("HOME_WATCH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    read = make_reader(PIN)
    start_http_server(METRICS_PORT, addr=METRICS_ADDR)
    log.info("serving metrics on %s:%s every %ss", METRICS_ADDR, METRICS_PORT, INTERVAL)

    # Schedule against a monotonic deadline so read time does not skew the interval.
    deadline = time.monotonic()
    while not stop.is_set():
        read_sensor(read, stop)
        deadline += INTERVAL
        now = time.monotonic()
        if deadline < now:  # reads overran the interval; resync instead of catching up
            deadline = now + INTERVAL
        stop.wait(deadline - now)

    log.info("shutting down")


if __name__ == "__main__":
    main()
