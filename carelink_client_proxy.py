###############################################################################
#  
#  Carelink Client Proxy
#  
#  Description:
#
#    This program periodically downloads the available data from the 
#    Medtronic Carelink API. Then this data is provided via a simple
#    REST API to local clients:
#
#    Send a GET request to the following URI: 
#      http://<serveraddr>:8081/carelink/          # all Carelink data
#      http://<serveraddr>:8081/carelink/nohistory # no history data
#  
#  Author:
#
#    Ondrej Wisniewski (ondrej.wisniewski *at* gmail.com)
#  
#  Changelog:
#
#    08/06/2021 - Initial public release
#    27/07/2021 - Add logging, bug fixes
#    06/02/2022 - Download new data as soon as it is available
#    08/02/2022 - Fix HTTP API
#    24/05/2023 - Add patient parameter
#
#  Copyright 2021-2023, Ondrej Wisniewski 
#
###############################################################################

import carelink_client
import argparse
import time
import json
import sys
import signal
import threading 
import syslog
import logging as log
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import HTTPStatus


VERSION = "0.6"

# Logging config
FORMAT = '[%(asctime)s:%(levelname)s] %(message)s'
log.basicConfig(format=FORMAT, datefmt='%Y-%m-%d %H:%M:%S', level=log.DEBUG)

# HTTP server settings
HOSTNAME = "0.0.0.0"
PORT     = 8081
BASEURI  = "carelink"
OPT_NOHISTORY = "nohistory"

UPDATE_INTERVAL = 300
RETRY_INTERVAL  = 120

recentData = None
verbose = False


#################################################
# The signal handler for the TERM signal
#################################################
def on_sigterm(signum, frame):
   # TODO: cleanup (if any)
   log.debug("exiting")
   syslog.syslog(syslog.LOG_NOTICE, "Exiting")
   sys.exit()


def get_essential_data(data):
   mydata = ""
   if data != None:      
      mydata = data.copy()
      try:
         del mydata["sgs"]
      except (KeyError,TypeError) as e:
         pass
      try:
         del mydata["markers"]
      except (KeyError,TypeError) as e:
         pass
      try:
         del mydata["limits"]
      except (KeyError,TypeError) as e:
         pass
      try:
         del mydata["notificationHistory"]
      except (KeyError,TypeError) as e:
         pass
   return mydata


#################################################
# HTTP server methods
#################################################
class MyServer(BaseHTTPRequestHandler):
   
   def log_message(self, format, *args):
      #Disable logging
      pass

   def do_GET(self):
      # Security checks (if any)
      # TODO
      log.debug("received client request from %s" % (self.address_string()))
      
      # Check request path
      if self.path.strip("/") == BASEURI:
         # Get latest Carelink data (complete)
         response = json.dumps(recentData)
         status_code = HTTPStatus.OK
         #print("All data requested")
      elif self.path.strip("/") == BASEURI+'/'+OPT_NOHISTORY:
         # Get latest Carelink data without history
         response = json.dumps(get_essential_data(recentData))
         status_code = HTTPStatus.OK
         #print("Only essential data requested")
      else:
         response = ""
         status_code = HTTPStatus.NOT_FOUND
      
      # Send response
      self.send_response(status_code)
      self.send_header("Content-type", "application/json")
      self.send_header("Access-Control-Allow-Origin", "*")
      self.end_headers()
      try:
         self.wfile.write(bytes(response, "utf-8"))
      except BrokenPipeError:
         pass
      

#################################################
# Web server thread
#################################################
def webserver_thread():
   # Init web server
   webserver = ThreadingHTTPServer((HOSTNAME, PORT), MyServer)
   log.debug("HTTP server started at http://%s:%s" % (HOSTNAME, PORT))
   #syslog.syslog(syslog.LOG_NOTICE, "HTTP server started at http://"+HOSTNAME+":"+str(PORT))

   # Start server loop
   webserver.serve_forever()


#################################################
# Start web server as asynchronous thread
#################################################
def start_webserver():
   t = threading.Thread(target=webserver_thread, args=())
   t.daemon = True
   t.start()


# Parse command line 
parser = argparse.ArgumentParser()
parser.add_argument('--username', '-u', type=str, help='CareLink username', required=True)
parser.add_argument('--password', '-p', type=str, help='CareLink password', required=True)
parser.add_argument('--country',  '-c', type=str, help='CareLink two letter country code', required=True)
parser.add_argument('--patient',  '-a', type=str, help='CareLink patient', required=True)
parser.add_argument('--wait',     '-w', type=int, help='Wait seconds between repeated calls (default 300)', required=False)
parser.add_argument('--verbose',  '-v', help='Verbose mode', action='store_true')
args = parser.parse_args()

# Get parameters
username = args.username
password = args.password
country  = args.country
patient  = args.patient
wait     = UPDATE_INTERVAL if args.wait == None else args.wait
verbose  = args.verbose

# Logging config (verbose)
if verbose:
   FORMAT = '[%(asctime)s:%(levelname)s] %(message)s'
   log.basicConfig(format=FORMAT, datefmt='%Y-%m-%d %H:%M:%S', level=log.DEBUG)
else:
   log.disable(level=log.DEBUG)

# Init syslog
syslog.openlog("carelink_client_proxy", syslog.LOG_PID|syslog.LOG_CONS, syslog.LOG_USER)
syslog.syslog(syslog.LOG_NOTICE, "Starting Carelink Client Proxy (version "+VERSION+")")

# Init signal handler
signal.signal(signal.SIGTERM, on_sigterm)
signal.signal(signal.SIGINT, on_sigterm)

# Start web server
start_webserver()

# Create Carelink client
client = carelink_client.CareLinkClient(username, password, country, patient)
log.debug("Client created!")

# First login to Carelink server
if client.login():
   # Infinite loop requesting Carelink data periodically
   i = 0
   while True:
      i += 1
      log.debug("Starting download " + str(i))
      try:
         for j in range(2):
            recentData = client.getRecentData()
            # Get success
            if client.getLastResponseCode() == HTTPStatus.OK:
               # Data OK
               if client.getLastDataSuccess():
                  log.debug("New data received")
               # Data error
               else:
                  print("Data exception: " + "no details available" if client.getLastErrorMessage() == None else client.getLastErrorMessage())
               break
            # Auth error
            elif client.getLastResponseCode() == HTTPStatus.FORBIDDEN:
               print("GetRecentData login error (status code FORBIDDEN). Trying again in 1 sec")
               time.sleep(1)
            else:
               print("Error, response code: " + str(client.getLastResponseCode()) + " Trying again in 1 sec")
               time.sleep(1)
      except Exception as e:
         print(e)
         syslog.syslog(syslog.LOG_ERR, "ERROR: %s" % (str(e)))
      
      # Calculate time until next reading
      if recentData != None:
         nextReading = int(recentData["lastConduitUpdateServerTime"]/1000) + wait
         tmoSeconds  = int(nextReading - time.time())
         #print("Next reading at {0}, {1} seconds from now\n".format(nextReading,tmoSeconds))
         if tmoSeconds < 0:
            tmoSeconds = RETRY_INTERVAL
      else:
         tmoSeconds = RETRY_INTERVAL
         #print("Retry reading {0} seconds from now\n".format(tmoSeconds))

      log.debug("Waiting " + str(tmoSeconds) + " seconds before next download!")
      time.sleep(tmoSeconds+10)
else:
   print("Client login error! Response code: " + str(client.getLastResponseCode()) + " Error message: " + str(client.getLastErrorMessage()))
   syslog.syslog(syslog.LOG_ERR,"Client login error! Response code: " + str(client.getLastResponseCode()) + " Error message: " + str(client.getLastErrorMessage()))
   syslog.syslog(syslog.LOG_ERR, "Emergency exit")
