#!/usr/bin/env python
import serial, sys, re
import logging
from logging.config import fileConfig

from datetime import datetime
from time import sleep
from httplib import HTTPSConnection
from threading import Thread, Event, currentThread  
import ssl  #inlfux
import minimalmodbus #rs485

import ConfigParser

import crcmod

import urllib
import urllib2
import base64

#read config
Config = ConfigParser.ConfigParser()
Config.readfp(open('/home/pi/scripts/aardehuis_nl_config.ini'))
Config.read('/home/pi/scripts/aardehuis_nl_config.ini')

# logging
fileConfig('/home/pi/scripts/aardehuis_nl_config.ini', )
logger = logging.getLogger()

#set minimalmodbus logging
def minimalModbusLogger(message):
   logger.debug(message)

minimalmodbus._print_out = minimalModbusLogger

# init COM port
ser          = serial.Serial()
ser.baudrate = Config.get('serial', 'baudrate')
ser.timeout  = int(Config.get('serial', 'timeout'))
ser.port     = Config.get('serial', 'port')
ser.open()

global meterID
meterID = None

#init rs485 
rs485 = minimalmodbus.Instrument( Config.get('rs485', 'Instrument'), 1)
rs485.mode =  minimalmodbus.MODE_RTU
rs485.serial.parity =  minimalmodbus.serial.PARITY_NONE
#rs485.debug = Config.get('rs485', 'debug')
rs485.serial.baudrate = int(Config.get('rs485', 'baudrate'))
rs485.serial.bytesize = int(Config.get('rs485', 'bytesize'))
rs485.serial.stopbits = int(Config.get('rs485', 'stopbits'))
rs485.serial.timeout = int(Config.get('rs485', 'timeout')) 


# Program variables
# The true telegram ends with an exclamation mark after a CR/LF
pattern = re.compile(b'\r\n(?=!)')
# According to the DSMR spec, we need to check a CRC16
crc16 = crcmod.predefined.mkPredefinedCrcFun('crc16')


'''
	emeter_energy
		tagKey:
			eqid	
			phase [total, L1, L2, L3]
			tarif [1,2]
			direction [in, out]
		values:
			energy [float/int]
bodyTemplate_power = 'emeter_power,eqid={eqid},tarif={tarif},direction={dir},phase={ph} value={value}\n'
body += bodyTemplate_power.format(	eqid=meterID,
									tarif=int(values['P1']['emeter_tarif_indicator']),
									dir=direction,
									ph=phase,
									value=values['P1'][i] )		
energy:
direction tarif
power:
direction phase
'''

options = {
'0-0:96.1.1': 'emeter_id', 
'0-0:1.0.0': 'emeter_time',
'0-0:96.14.0': 'emeter_tarif_indicator',

'1-0:1.8.1': 'energy_in_1' , 	
'1-0:1.8.2': 'energy_in_2' , 	
'1-0:2.8.1': 'energy_out_1' , 
'1-0:2.8.2': 'energy_out_2', 

'1-0:1.7.0': 'power_in_total', 
'1-0:2.7.0': 'power_out_total',
'1-0:21.7.0': 'power_in_L1',
'1-0:41.7.0': 'power_in_L2',                                   
'1-0:61.7.0': 'power_in_L3',
'1-0:22.7.0': 'power_out_L1',
'1-0:42.7.0': 'power_out_L2',
'1-0:62.7.0': 'power_out_L3',
}
regex = re.compile(r'[^0-9]*$')  # remove non-digits at the end

def readP1(stop_event ):
	logging.info( 'Starting %s' % currentThread().getName())
	''' read P1, collect values from p1 telegram, return values ''' 
	#set meterid/id

	while stop_event.is_set():
		logger.info("reading P1")
		tagStack = options.keys()
		ret = {}

		telegram = ''
		checksum_found = False

		while not checksum_found:
			# Read in a line
			telegram_line = ser.readline()

			# Check if it matches the start line (/ at start)
			if re.match(b'(?=/)', telegram_line):
				telegram = telegram + telegram_line
				logger.debug('Found start!')
				while not checksum_found:
					telegram_line = ser.readline()
					# Check if it matches the checksum line (! at start)
					if re.match(b'(?=!)', telegram_line):
						telegram = telegram + telegram_line
						logger.debug('Found checksum!')
						checksum_found = True
					else:
						telegram = telegram + telegram_line

		# print telegram

		# We have a complete telegram, now we can process it.
		# Look for the checksum in the telegram
		good_checksum =  False
		for m in pattern.finditer(telegram):
			# Remove the exclamation mark from the checksum,
			# and make an integer out of it.
			given_checksum = int('0x' + telegram[m.end() + 1:].decode('ascii'), 16)
			# The exclamation mark is also part of the text to be CRC16'd
			calculated_checksum = crc16(telegram[:m.end() + 1])
			if given_checksum == calculated_checksum:
				good_checksum = True
			
			if good_checksum:
				logger.info("Good checksum!")
	
				try: 
					while tagStack:
					
						for raw in telegram.split(b'\r\n'):

							#logging.debug('raw telegram: {}'.format(raw))
							tag = raw.split('(')[0]
				
							if tag in options.keys():
								value = re.sub(regex, '', raw.split('(')[1])
								logger.debug("found tag: {} value: {}".format(tag,value) )
				
								if tag == '0-0:96.1.1':
									ret[options[tag]] = str(value).decode('hex')
									# set globel meterID
									global meterID
									meterID = ret[options[tag]]
								elif tag == '0-0:1.0.0':
									ret[options[tag]] = tijdomvormer(value)
								else:
									ret[options[tag]] = value 

								tagStack.remove(tag)
					
					logger.debug('P1 return value: {}'.format(ret))
					postP1( ret, stop_event )
				except:
					logger.debug('P1 read error')

			else:
				logger.info("No Good, next!")

def postP1(values, stop_event):
	'''
	'0-0:96.1.1': 'emeter_id',  	  		eqid
	'1-0:1.8.1': 'energy_in_1', 	  		tariff=1, direction=in, unit = kWh
	'1-0:1.8.2': 'energy_in_2',   			tariff=2, direction=in, unit = kWh
	'1-0:2.8.1': 'energy_out_1',  			tariff=1, direction=out, unit = kWh
	'1-0:2.8.2': 'energy_out_2', 			tariff=2, direction=out, unit = kWh
	'0-0:1.0.0': 'emeter_time',				meter time
	'0-0:96.14.0': 'emeter_tariff_indicator',	tariff	
	'1-0:1.7.0': 'power_in_total',			tariff= emeter_tariff_indicator, direction=in, phase=total , unit = kW (+P)
	'1-0:2.7.0': 'power_out_total',			tariff= emeter_tariff_indicator, direction=out, phase=total , unit = kW
	'1-0:21.7.0': 'power_in_L1',			tariff= emeter_tariff_indicator, direction=in, phase=one, unit = kW
	'1-0:41.7.0': 'power_in_L2',			tariff= emeter_tariff_indicator, direction=in, phase=two, unit = kW
	'1-0:61.7.0': 'power_in_L3',			tariff= emeter_tariff_indicator, direction=in, phase=three , unit = kW
	'1-0:22.7.0': 'power_out_L1',			tariff= emeter_tariff_indicator, direction=out, phase=one, unit = kW
	'1-0:42.7.0': 'power_out_L2',			tariff= emeter_tariff_indicator, direction=out, phase=two, unit = kW
	'1-0:62.7.0': 'power_out_L3',			tariff= emeter_tariff_indicator, direction=out, phase=three, unit = kW
	'''

	if meterID and stop_event.is_set():

			tarif = int(values['emeter_tarif_indicator'])
			etime = int(values['emeter_time']) * 1000000000

			body=''
			bodyTemplate_energy = 'emeter_energy,eqid={eqid},tarif={tarif},direction={dir} value={value} {etime}\n'
			bodyTemplate_power = 'emeter_power,eqid={eqid},tarif={tarif},direction={dir},phase={phase} value={value} {etime}\n'

			for k,v in options.items():
				splitted = v.split('_')
				if splitted[0] == 'power':
					# meterstanden acuteel in kW )(power)
					body += bodyTemplate_power.format(	eqid=meterID,
										tarif=tarif,
										dir=splitted[1],
										phase=splitted[2],
										value=values[v],
										etime=etime )
					# meterstanden acuteel in kW )(power)
					if splitted[2] == 'total':
						if splitted[1] == 'in':
							cons = int(float(values[v])*1000)
							#print float(values[v])*1000
						else:
							prod = int(float(values[v])*1000)
							#print float(values[v])*1000
				elif splitted[0] == 'energy':
					#meterstanden  cummulitieven in kWh (energy)
					body += bodyTemplate_energy.format(	eqid=meterID, 
										dir=splitted[1],
										tarif=splitted[2],
										value=values[v],
										etime=etime )
					#print splitted[1], splitted[2], float(values[v])*1000
					#meterstanden  cummulitieven in kWh (energy)
					if splitted[1] == 'in':
						if splitted[2] == '1':
							usage1 = int(float(values[v])*1000)
						else:
							usage2 = int(float(values[v])*1000)
					else:
						if splitted[2] == '1':
							return1 = int(float(values[v])*1000)
						else:
							return2 = int(float(values[v])*1000)

			#Domoticz
			IDX = 11
			svalue = str(usage1)+';'+str(usage2)+';'+str(return1)+';'+str(return2)+';'+str(cons)+';'+str(prod)
	
			logging.debug('post body: \n{}'.format(body))
			InfluxPost(body)
			DomoticzPost( svalue, IDX )
			sleep(10)

def readRS485(stop_event ):
	''' read DSM120 powermeter over rs485 '''
	logging.info( 'Starting %s' % currentThread().getName())
	while stop_event.is_set():
		ret = {}
		try:
			Activepower =  rs485.read_float( 12, functioncode=4, numberOfRegisters=2)
			sleep(1)
			TotalPower =  rs485.read_float( 342, functioncode=4, numberOfRegisters=2)
		except IOError as err:
			logging.debug( 'Ooops, rs458 hickups %s' % err)
			pass
		else:
			ret['sol_pow'] = float(Activepower)
			ret['sol_nrg'] = float(TotalPower)

			svalue = str(int(float(Activepower)*-1)) +';'+ str(int(float(TotalPower))*1000)
			IDX = 13

			logger.debug('rs485 return value: {}'.format(ret))
			postRS485( ret, stop_event ) 
			DomoticzPost( svalue, IDX )
		sleep(9)

def postRS485(values, stop_event):
	if stop_event.is_set() and len(values) == 2:
		# solar power / energy, pass if not both there
		bodyTemplate_solar = 'emeter_solar,eqid={eqid},type={type} value={value}\n'

		body  = bodyTemplate_solar.format(eqid=meterID,type='cumulative', value=values['sol_nrg'])
		body += bodyTemplate_solar.format(eqid=meterID,type='instant', value=values['sol_pow'])

		logging.debug('post body: \n{}'.format(body))
		InfluxPost(body)

def InfluxPost(body):
	host 		= Config.get('influxdb', 'influxHost')
	port 		= Config.get('influxdb', 'port')
	wachtwoord 	= Config.get('influxdb', 'wachtwoord')
	username	= Config.get('influxdb', 'username')
	dbname 		= Config.get('influxdb', 'dbname')
	context = ssl._create_unverified_context()
	conn = HTTPSConnection(host,port,context=context)
	headers = {'Content-type': 'application/x-www-form-urlencoded','Accept': 'text/plain'}

	#except Exception as e:
	#	logging.info('Ooops! something went wrong with creating HTTP object! {}'.format(e))

	conn.set_debuglevel(7)
	try:
		#dbname='db_name'
		conn.request('POST', '/write?db={db}&u={user}&p={password}'.format(db=dbname, user=username, password=wachtwoord), body, headers) 
	except Exception as e:
		logging.info('Ooops! something went wrong with POSTing {}'.format(e))
		pass
	else:
		response = conn.getresponse()
		logging.info('Updated Influx. HTTP response {}'.format(response.status))
	finally:
		conn.close()

		logging.debug("Reason: {}\n Response:{}".format(response.reason, response.read) )
	conn.close()

def DomoticzPost(svalue, IDX):
	domoticz_host 	= Config.get('domoticz', 'domoticzHost')
	domoticz_port 	= Config.get('domoticz', 'port')
	domoticz_url	= Config.get('domoticz', 'url')
	wachtwoord 	= Config.get('domoticz', 'wachtwoord')
	username	= Config.get('domoticz', 'username')

	url = ("http://" + domoticz_host + ":" + domoticz_port + "/" + domoticz_url)

	get_data = {
		'svalue': svalue,
		'type': 'command',
		'param': 'udevice',
		'idx' : str(IDX),
		'nvalue': '0',
	}

	get_data_encoded = urllib.urlencode(get_data)
	request_object = urllib2.Request(url + '?' + get_data_encoded)
	base64string = base64.b64encode('%s:%s' % (username, wachtwoord))
	request_object.add_header("Authorization", "Basic %s" % base64string)   
	try:
		response = urllib2.urlopen(request_object)
	except urllib2.URLError as e:
		if hasattr(e, 'reason'):
			print 'Failed to reach a server.'
			print 'Reason: ', e.reason
		elif hasattr(e, 'code'):
			print 'The server couldn\'t fulfill the request.'
			print 'Error code: ', e.code
	else:
		#everything is fine
		logging.info('Updated Domoticz. HTTP response {}'.format(response.read()))
		
def ConfigSectionMap(section):
	dict1 = {}
	options = Config.options(section)
	for option in options:
		try:
			dict1[option] = Config.get(section, option)
			if dict1[option] == -1:
				DebugPrint("skip: %s" % option)
		except:
			logging.info("Config: exception on %s!" % option)
			dict1[option] = None
	return dict1

def tijdomvormer(timestamp):
	year	= '20' + timestamp[0:2]
	month	= timestamp[2:4]
	day		= timestamp[4:6]
	hour	= timestamp[6:8] 
	minutes	= timestamp[8:10]
	seconds	= timestamp[10:12] 
	return datetime(*map(int,( year, month, day, hour, minutes, seconds ) )).strftime('%s')  # epoch				

def main():
	logger.info("Starting main thread")

	pill2kill = Event()
	pill2kill.set()
	P1reader = Thread(name='P1reader', target=readP1, args=(pill2kill, ))
	rs485reader = Thread(name='rs485reader', target=readRS485, args=(pill2kill, ))
	#P1reader.setDaemon = True
	P1reader.start()

	rs485reader.start()
	#GPIO.add_event_detect(24, GPIO.RISING, callback=waterMeter, bouncetime=1000)  # water meter
	try:
		while 1:
			sleep (10)
	except KeyboardInterrupt:
		print '\n attempting to close threads.\n {filler}\n'.format( filler = 60*'=' )
		pill2kill.clear()
		P1reader.join()
		sys.exit()
	
if __name__ == '__main__': 
	main()
