#!/usr/bin/env python
from Queue import Queue
import serial, sys, re
import logging
from logging.config import fileConfig


from datetime import datetime
from time import sleep
from threading import Thread, Event, currentThread  
import ssl, httplib  #inlfux
import minimalmodbus #rs485

import ConfigParser

#read config
Config = ConfigParser.ConfigParser()
Config.readfp(open('/home/leen/scripts/aardehuis_nl_config.ini'))
Config.read('/home/leen/scripts/aardehuis_nl_config.ini')

# logging
fileConfig('/home/leen/scripts/aardehuis_nl_config.ini', )
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
# init Queue
Q = Queue()


#init rs485 
rs485 = minimalmodbus.Instrument( Config.get('rs485', 'Instrument'), 1)
rs485.mode =  minimalmodbus.MODE_RTU
rs485.serial.parity =  minimalmodbus.serial.PARITY_NONE
rs485.debug = Config.get('rs485', 'debug')
rs485.serial.baudrate = int(Config.get('rs485', 'baudrate'))
rs485.serial.bytesize = int(Config.get('rs485', 'bytesize'))
rs485.serial.stopbits = int(Config.get('rs485', 'stopbits'))
rs485.serial.timeout = int(Config.get('rs485', 'timeout')) 

'''
	emeter_energy
		tagKey:
			eqid	
			phase [total, L1, L2, L3]
			tarif [1,2]
			direction [in, out]
		values:
			energy [float/int]

'{ms},id={id}, {field}={value}\n '.format(ms='emeter_energy', id='eqid',phase='', tarif='', direction='' )
'''

measurementsTags = { 	'emeter_energy': ['phase', 'eqid', 'tarif'],
				'emeter_pow':['phase', 'eqid', 'tarif'],
				'solar_energy': ['eqid'],
				'solar_power': ['eqid'] 
			}

options = {
'0-0:96.1.1': 'emeter_id', 
'1-0:1.8.1': 'emeter_low_in' , 	
'1-0:1.8.2': 'emeter_high_in' , 	
'1-0:2.8.1': 'emeter_low_out' , 
'1-0:2.8.2': 'emeter_high_out', 
'1-0:1.7.0': 'emeter_pow_in', 
'1-0:2.7.0': 'emeter_pow_out',
'0-0:1.0.0': 'emeter_time',

'1-0:21.7.0': 'emeter_in_one',
'1-0:41.7.0': 'emeter_in_two',
'1-0:61.7.0': 'emeter_in_three',
'1-0:22.7.0': 'emeter_out_one',
'1-0:42.7.0': 'emeter_out_two',
'1-0:62.7.0': 'emeter_out_three',
'0-0:96.14.0': 'emeter_tarif_indicator',
}
regex = re.compile(r'[^0-9]*$')  # remove non-digits at the end

def readP1(stop_event, Q):
	logging.info( 'Starting %s' % currentThread().getName())
	''' read P1, collect values from p1 telegram, return values ''' 
	#set meterid/id

	while stop_event.is_set():
		logger.info("reading P1")
		tagStack = options.keys()
		ret = {}
		while tagStack:
			raw = ser.readline()
			logging.debug('raw telegram: {}'.format(raw))
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
		returnFinal = {}
		returnFinal = { 'P1' : ret}
		logger.debug('P1 return value: {}'.format(returnFinal))
		Q.put( returnFinal )
		sleep(10)

def readRS485(stop_event, Q):
	''' read DSM120 powermeter over rs485 '''
	logging.info( 'Starting %s' % currentThread().getName())
	while stop_event.is_set():
		ret = {}
		try:
			Activepower =  rs485.read_float( 12, functioncode=4, numberOfRegisters=2)
			sleep(1)
			TotalPower =  rs485.read_float( 342, functioncode=4, numberOfRegisters=2)
			sleep(1)
		except IOError as err:
			logging.debug( 'Ooops, rs458 hickups %s' % err)
			pass
		else:
			ret['sol_pow'] = float(Activepower)
			ret['sol_nrg'] = float(TotalPower)
		returnFinal = {}
		returnFinal = { 'solar' : ret}
		logger.debug('rs485 return value: {}'.format(ret))
		Q.put( returnFinal ) 
		sleep (8)

def updateInflux(values):
	
	if meterID:
		dbname 		= Config.get('influxdb', 'dbname')
		measurement = Config.get('influxdb', 'measurement')
		host 		= Config.get('influxdb', 'influxHost')
		port 		= Config.get('influxdb', 'port')
		huisID		= Config.get('influxdb', 'huisID' )
		wachtwoord 	= Config.get('influxdb', 'wachtwoord')
		username	= Config.get('influxdb', 'username')


		context = ssl._create_unverified_context()
		headers = {'Content-type': 'application/x-www-form-urlencoded','Accept': 'text/plain'}
		try:
			conn = httplib.HTTPSConnection(host,port,context=context)
		except Exception as e:
			logging.debug('Ooops! something went wrong with the HTTP connection! {}'.format(e))

		'''
		'0-0:96.1.1': 'emeter_id',  	  			eqid
		'1-0:1.8.1': 'emeter_low_in' , 	  			tariff=1, direction=in, unit = kWh
		'1-0:1.8.2': 'emeter_high_in' ,   			tariff=2, direction=in, unit = kWh
		'1-0:2.8.1': 'emeter_low_out' ,  			tariff=1, direction=out, unit = kWh
		'1-0:2.8.2': 'emeter_high_out', 			tariff=2, direction=out, unit = kWh

		'0-0:1.0.0': 'emeter_time',					meter time
		'0-0:96.14.0': 'emeter_tariff_indicator',   tariff	


		'''
		if 'P1' in values.keys():
			#meterstanden  cummulitieven in kWh (energy)
			body  = '{ms},eqid={eqid},tarif={tarif},direction={dir} value={value}\n'.format(ms='emeter_energy', 
																							eqid=meterID, 
																							ph='total',
																							dir='in',
																							tarif=1,
																							value=values['P1']['emeter_low_in'] )
			body += '{ms},eqid={eqid},tarif={tarif},direction={dir} value={value}\n'.format(ms='emeter_energy',
																							eqid=meterID,
																							dir='in',
																							tarif=2,
																							value=values['P1']['emeter_high_in'] )
			body += '{ms},eqid={eqid},tarif={tarif},direction={dir} value={value}\n'.format	(ms='emeter_energy',
																							eqid=meterID,
																							dir='out',
																							tarif=1,
																							value=values['P1']['emeter_low_out'] )
			body += '{ms},eqid={eqid},tarif={tarif},direction={dir} value={value}\n'.format(ms='emeter_energy',
																							eqid=meterID,
																							dir='out',
																							tarif=2,
																							value=values['P1']['emeter_high_out'] )


			'''
			'1-0:1.7.0': 'emeter_pow_in', 				tariff= emeter_tariff_indicator, direction=in, phase=total , unit = kW (+P)
			'1-0:2.7.0': 'emeter_pow_out',				tariff= emeter_tariff_indicator, direction=out, phase=total , unit = kW

			'1-0:21.7.0': 'emeter_in_one',				tariff= emeter_tariff_indicator, direction=in, phase=one, unit = kW
			'1-0:41.7.0': 'emeter_in_two',				tariff= emeter_tariff_indicator, direction=in, phase=two, unit = kW
			'1-0:61.7.0': 'emeter_in_three',			tariff= emeter_tariff_indicator, direction=in, phase=three , unit = kW
			'1-0:22.7.0': 'emeter_out_one',				tariff= emeter_tariff_indicator, direction=out, phase=one, unit = kW
			'1-0:42.7.0': 'emeter_out_two',				tariff= emeter_tariff_indicator, direction=out, phase=two, unit = kW
			'1-0:62.7.0': 'emeter_out_three',			tariff= emeter_tariff_indicator, direction=out, phase=three, unit = kW

			'''
			# meterstanden instant in kW (power)
			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(	ms='emeter_power',
																										eqid=meterID,
																										ph='total',
																										dir='in',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_pow_in'] )			
			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(	ms='emeter_power',
																										eqid=meterID,
																										ph='total',
																										dir='out',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_pow_out'] )


			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(	ms='emeter_power',
																										eqid=meterID,
																										ph='L1',
																										dir='in',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_in_one'] )
			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(	ms='emeter_power',
																										eqid=meterID,
																										ph='L2',
																										dir='in',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_in_two'] )
			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(	ms='emeter_power',
																										eqid=meterID,
																										ph='L3',
																										dir='in',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_in_three'] )

			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(	ms='emeter_power',
																										eqid=meterID,
																										ph='L1',
																										dir='out',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_out_one'] )
			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(	ms='emeter_power',
																										eqid=meterID,
																										ph='L2',
																										dir='out',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_out_two'] )
			body += '{ms},eqid={eqid},phase={ph},tarif={tarif},direction={dir} value={value}\n'.format(ms='emeter_power',
																										eqid=meterID,
																										ph='L3',
																										dir='out',
																										tarif=int(values['P1']['emeter_tarif_indicator']),
																										value=values['P1']['emeter_out_three'] )
			print body
			#if logLevel == 'logging.DEBUG':
			conn.set_debuglevel(7)
			try:
				dbname='db_name'
				conn.request('POST', '/write?db={db}&u={user}&p={password}'.format(db=dbname, user=username, password=wachtwoord), body, headers) 
			except Exception as e:
				log.debug('Ooops! something went wronh with POSTing {}'.format(e))
				pass
			finally:
				response = conn.getresponse()
				logging.info('Updated Influx. HTTP response {}'.format(response.status))
				conn.close()


				#logging.debug("Reason: {}\n Response:{}".format(response.reason, response.read) )
			conn.close()
		else:
			pass  # pass if something other then P1
	else:
		pass # pass if meterId not set

		
def ConfigSectionMap(section):
	dict1 = {}
	options = Config.options(section)
	for option in options:
		try:
			dict1[option] = Config.get(section, option)
			if dict1[option] == -1:
				DebugPrint("skip: %s" % option)
		except:
			print("exception on %s!" % option)
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

def processQ ( stop_event, Q ) :
	''' processes content of the Q, updates influx '''
	logging.info( 'Starting %s' % currentThread().getName())
	while stop_event.is_set() and Q:

		logging.info( 'Starting %s' % currentThread().getName())
		vals = Q.get()
		logging.debug("get Q: {}".format(vals))


		updateInflux(vals)
		Q.task_done()



def main():
	logger.info("Starting main thread")


	pill2kill = Event()
	pill2kill.set()
	readerWorker = Thread(name='readerWorker', target=readP1, args=(pill2kill, Q,))
	processQWorker = Thread(name='processQWorker', target=processQ, args=(pill2kill, Q,))
	rs485reader = Thread(name='rs485reader', target=readRS485, args=(pill2kill, Q,))
	#readerWorker.setDaemon = True
	readerWorker.start()

	#processQWorker.setDaemon = True
	processQWorker.start()
	rs485reader.start()
	#GPIO.add_event_detect(24, GPIO.RISING, callback=waterMeter, bouncetime=1000)  # water meter
	try:
		while 1:
			sleep (10)
	except KeyboardInterrupt:
		print "attempting to close threads. "
		pill2kill.clear()
		processQWorker.join()
		readerWorker.join()
		sys.exit()
	
if __name__ == '__main__': 
	main()






