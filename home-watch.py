"""Read a DHT22/AM2302 sensor and expose the readings as Prometheus metrics."""

import glob
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


PIN = _env_int("HOME_WATCH_PIN", 12)  # BCM numbering; only used by the gpio backend
METRICS_PORT = _env_int("HOME_WATCH_METRICS_PORT", 9101)
METRICS_ADDR = os.environ.get("HOME_WATCH_METRICS_ADDR", "0.0.0.0")
INTERVAL = _env_int("HOME_WATCH_INTERVAL", 15)
MAX_ATTEMPTS = _env_int("HOME_WATCH_MAX_ATTEMPTS", 3)
BACKEND = os.environ.get("HOME_WATCH_BACKEND", "auto").lower()
# The kernel driver caches a reading for 2s and the datasheet wants 2s between
# samples, so retry no faster than that.
RETRY_DELAY = 2.1

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


def find_iio_device():
    """Return the sysfs directory of the kernel dht11 IIO device, or None."""
    override = os.environ.get("HOME_WATCH_IIO_PATH")
    if override:
        return override
    for path in sorted(glob.glob("/sys/bus/iio/devices/iio:device*")):
        try:
            with open(os.path.join(path, "name")) as handle:
                name = handle.read().strip()
        except OSError:
            continue
        if name == "dht11":  # the dht11 driver also drives the DHT22/AM2302
            return path
    return None


def make_iio_reader(device):
    """Read through the kernel dht11 driver: no GPIO library, no root, no busy-wait."""
    temperature_file = os.path.join(device, "in_temp_input")
    humidity_file = os.path.join(device, "in_humidityrelative_input")

    def read_milli(path):
        try:
            with open(path) as handle:
                return int(handle.read().strip())
        except OSError as exc:
            # EIO is a checksum mismatch, ETIMEDOUT means the sensor never
            # answered. Both are routine and worth retrying. str(exc) already
            # names the file.
            raise SensorError(str(exc))
        except ValueError as exc:
            raise SensorError("unparseable value in {0}: {1}".format(path, exc))

    def read():
        # The driver caches one frame for 2s, so these two reads share a single
        # sample of the sensor rather than triggering two.
        humidity = read_milli(humidity_file) / 1000.0
        temperature = read_milli(temperature_file) / 1000.0
        return humidity, temperature

    return read


def make_gpio_reader(pin):
    """Read by bit-banging the GPIO line from userspace via Blinka."""
    try:
        import adafruit_dht
        import board
    except ImportError as exc:
        raise SystemExit(
            "the gpio backend needs the optional dependencies: "
            "uv run --extra gpio home-watch.py ({0})".format(exc)
        )

    # use_pulseio=False selects the bitbang driver, which is the path that works
    # on current Raspberry Pi OS releases without the pulseio kernel bits.
    device = adafruit_dht.DHT22(getattr(board, "D{0}".format(pin)), use_pulseio=False)

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


def make_reader():
    if BACKEND not in ("auto", "iio", "gpio"):
        raise SystemExit(
            "HOME_WATCH_BACKEND must be auto, iio or gpio, not {0!r}".format(BACKEND)
        )

    if BACKEND in ("auto", "iio"):
        device = find_iio_device()
        if device is not None:
            log.info("using the kernel dht11 driver at %s", device)
            return make_iio_reader(device)
        if BACKEND == "iio":
            raise SystemExit(
                "no dht11 IIO device found. Add 'dtoverlay=dht11,gpiopin={0}' to "
                "/boot/firmware/config.txt and reboot.".format(PIN)
            )
        log.warning(
            "no dht11 IIO device found, falling back to userspace GPIO on pin %s", PIN
        )

    return make_gpio_reader(PIN)


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

    read = make_reader()
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
