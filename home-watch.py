import Adafruit_DHT
import prometheus_client
from time import sleep
import datetime

sensor = Adafruit_DHT.AM2302
pin = 12
metrics_port = 9101

temprature_gauge = prometheus_client.Gauge('current_temprature', 'Current temprature in celcius')
humidity_gauge = prometheus_client.Gauge('current_humidity', 'Current humidity as a percentage value')

def read_sensor():
    dt = datetime.datetime.now()
    humidity = temprature = None
    humidity, temprature = Adafruit_DHT.read_retry(sensor, pin)
    if humidity != None and temprature != None:
        temprature_gauge.set(temprature)
        humidity_gauge.set(humidity)
        print("{0} - Humidity: {1}, Temprature: {2}\n".format(dt, humidity, temprature))
    else:
        print("Failed to read sensor")


def main():
    prometheus_client.start_http_server(metrics_port)
    while True:
        read_sensor()
        sleep(10)

if __name__ == "__main__":
    main()