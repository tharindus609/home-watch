import Adafruit_DHT
from time import sleep
import datetime

sensor = Adafruit_DHT.AM2302
pin = 12

try:
    while True:
        hum = None
        temp = None
        while hum == None or temp == None:
            hum, temp = Adafruit_DHT.read_retry(sensor, pin)
        dt = datetime.datetime.now()
        print("{0} - Humidity: {1}, Temprature: {2}\n".format(dt, hum, temp))
        sleep(10)
except KeyboardInterrupt:
    exit(0)