Create a config file config.ini, and set correct path/filename in script.
# config example:
```
[influxdb]
dbname =  
measurement =  
influxHost = 
port = 
huisID = 
wachtwoord = 
username = 

[serial]
baudrate = 115200
timeout = 8
port = /dev/ttyAMA0

[rs485]
Instrument = /dev/ttyUSB0
debug = False
baudrate = 9600
bytesize = 8
stopbits = 1
timeout = 5

[loggers]
keys=root

[handlers]
keys=stream_handler,file_handler

[formatters]
keys=formatter

[logger_root]
level=DEBUG
handlers=stream_handler,file_handler

[handler_stream_handler]
class=StreamHandler
level=INFO
formatter=formatter
args=(sys.stderr,)

[handler_file_handler]
class=RotatingFileHandler
level=DEBUG
formatter=formatter
args=('debug.log', maxBytes=200000, backupCount=5)


[formatter_formatter]
format=%(asctime)s %(name)-12s %(levelname)-8s %(message)s
```
