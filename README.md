# weather

Simple weather monitoring Python script

## Install

- needs OWFS:
```
    sudo apt-get -y install python3-ow
```
- clone the repository to `/srv/`:
```
    git clone https://github.com/vladak/weather.git /srv/weather
```
- copy `weather.service` file to `/etc/systemd/system/weather.service`:
- enable the service:
```
sudo systemctl enable weather
```
- if the file `/etc/systemd/system/weather.service` changes, run:
```
sudo systemctl daemon-reload
```
- to start the service:
```
sudo systemctl start weather
sudo systemctl status weather
```
- install `logrotate` config:
```
cp temperature.logrotate /etc/logrotate.d/temperature
```

### Telegraf

To make the logs available in InfluxDB via Telegraf:
```
cp weather.conf /etc/telegraf/telegraf.d/weather.conf
sudo service telegraf restart
```

#### Debugging tips

To debug log parsing in Telegraf, use https://grokdebug.herokuapp.com/

Then to see it actually works, it is necessary to run `telegraf` without the
`--test` option like so with config file trimmed so that it contains just
the logparser section and output section that goes to standard output:

```
[[inputs.logparser]]
   ## file(s) to read:
   # files = ["/var/log/temperature.log"]
   files = ["temperature.log"]

   # Only send these fields to the output plugins
   fieldpass = ["kuchyne", "terasa", "timestamp"]
   # fieldpass = ["temperature", "humidity", "timestamp"]
   tagexclude = ["path"]

   # Read the file from beginning on telegraf startup.
   from_beginning = true
   # name_override = "room_temperature_humidity"

   ## For parsing logstash-style "grok" patterns:
   [inputs.logparser.grok]
     patterns = ["%{TEMPERATURE_PATTERN}"]
     custom_patterns = '''
TEMPERATURE_PATTERN ^%{TIMESTAMP_ISO8601:timestamp:ts-"2006-01-02 15:04:05-0700"} %{MYGREEDYDATA} %{TELEGRAF_SEPARATOR} %{WORD}=%{NUMBER:kuchyne:float} %{WORD}=%{NUMBER:terasa:float}
MYGREEDYDATA [^\|]*
TELEGRAF_SEPARATOR \| telegraf:
'''

[[outputs.file]]
   files = ["stdout"]
```

Then run as follows:
```
telegraf --config weather-debug.conf
```

It should report:
```
2019-08-01T13:52:01Z I! Starting Telegraf 1.11.3
2019-08-01T13:52:01Z I! Loaded inputs: logparser
2019-08-01T13:52:01Z I! Loaded aggregators:
2019-08-01T13:52:01Z I! Loaded processors:
2019-08-01T13:52:01Z I! Loaded outputs: file
2019-08-01T13:52:01Z I! Tags enabled: host=raspberrypi
2019-08-01T13:52:01Z I! [agent] Config: Interval:10s, Quiet:false, Hostname:"raspberrypi", Flush Interval:10s
logparser,host=raspberrypi kuchyne=28.8125,terasa=23.125 1564523781000000000
```

Note it is necessary to wait for the flush interval to expire before any output
appears - in this case 10 seconds.
