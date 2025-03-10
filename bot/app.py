
import os
import sys
import time
import json
import glob
import stat
import atexit
import ifaddr
import signal
import socket
import shutil
import platform
import urllib.request
from flask import Flask, request, jsonify, send_from_directory, send_file, Response
from threading import Thread
from subprocess import Popen, PIPE, STDOUT
from wpasupplicantconf import WpaSupplicantConf

# load the clock (can be an emulator)
from clock.botclock import BotClock
from clock.botAnimations import *

import re

def check_raspi():
  """Detect if it's a Raspberry Pi"""
  # Check /proc/cpuinfo for the Hardware field value.
  # 2708 is pi 1, 2709 is pi 2, 2835 is pi 3 on 4.9.x kernel, anything else is not a pi.
  try:
    with open('/proc/cpuinfo', 'r') as infile:
        cpuinfo = infile.read()
    # Match a line like 'Hardware   : BCM2709'
    match = re.search(r'^Hardware\s+:\s+(\w+)$', cpuinfo, flags=re.MULTILINE | re.IGNORECASE)
    if not match:
        return False
    if match.group(1) == 'BCM2708' or match.group(1) == 'BCM2709' or match.group(1) == 'BCM2835':
        return True
    else:
        # Something else, not a pi.
        return False
  except:
    return False

app = Flask(__name__)
# set cache for static files globally to 24 hours
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 60*60*24

# No cacheing at all for API endpoints.
@app.after_request
def add_header(response):
    # response.cache_control.no_store = True
    if 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'no-store'
    return response

# determine if this script is running on a raspberry pi (Linux with arm processor)
isRaspi= check_raspi()

clock = None
rootFolder = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
bkupFolder= os.path.dirname(rootFolder)+ '/.bkup'
webFolder = rootFolder+ '/bot'
i18nFolder = webFolder+ '/locales/'
clockFolder = rootFolder+ '/bot/clock'
soundsFolder= clockFolder+ '/sounds'
wifiFolder = '/etc/wpa_supplicant/'
configFilename= 'config.json'
# if script is not running on raspi, use the local version of wpa_supplicant.conf file
if not isRaspi:
    wifiFolder= rootFolder+ '/raspi-setup'

# get current application source version to use as parameter in loading files
curVersion= {}
with open(webFolder+ '/version.json', 'r') as f:
    curVersion= json.loads(f.read())
appVersion= curVersion.get('version', 0)


def handleFileError(func, path, exc_info):
    # Check if file access issue
    if not os.access(path, os.W_OK):
       # Try to change the permision of file
       os.chmod(path, stat.S_IWUSR)
       # call the calling function again
       func(path)

def checkOrRestoreConfigFile():
    curConfigFile= os.path.join(clockFolder, configFilename)
    bkupConfigFile= os.path.join(bkupFolder+ '/beamOfTime/bot/clock', configFilename)
    setupConfigFile= os.path.join(rootFolder+ '/raspi-setup/', configFilename)
    if not os.path.isfile(curConfigFile) or os.path.getsize(curConfigFile) < 100:
        if os.path.isfile(bkupConfigFile):
            shutil.copy(bkupConfigFile, curConfigFile)
        else:
            shutil.copy(setupConfigFile, curConfigFile)


@app.route('/')
@app.route('/index')
@app.route('/index.html')
def index():
    res= ''
    with open(webFolder+ '/index.html', 'r') as f:
        res= f.read()

    res= res.replace('[version]', str(appVersion))
    if not isRaspi:
        res= res.replace('<!--isRaspi-->', '<!--').replace('<!--/isRaspi-->', '-->')
        res= res.replace('<!--isDev ', '').replace(' /isDev-->', '')
    return res
    
@app.route('/favicon.ico')
def send_favicon():
    return send_from_directory(webFolder+ '/files', 'favicon.ico', mimetype="image/x-icon")

@app.route('/js/<path:path>')
def send_js(path):
    # request may have multiple files comma separated, combine all of them into one result
    path_array= path.split(',')
    res = ''
    for fname in path_array:
        # skip version variable
        if len(fname.strip())== 0:
            continue
        with open(webFolder+ '/js/'+ fname.strip(), 'r') as f:
            res += ('\n' + f.read())

    res= res.replace('[version]', str(appVersion))
    return Response(res, mimetype='application/javascript')

@app.route('/css/<path:path>')
def send_css(path):
    # request may have multiple files comma separated, combine all of them into one result
    path_array= path.split(',')
    res = ''
    for fname in path_array:
        # skip version variable
        if len(fname.strip())== 0:
            continue
        with open(webFolder+ '/css/'+ fname.strip(), 'r') as f:
            res += ('\n' + f.read())
    return Response(res, mimetype='text/css')

@app.route('/pages/<path:path>')
def send_pages(path):
    # request may have multiple files comma separated, combine all of them into one result
    path_array= path.split(',')
    res = ''
    for fname in path_array:
        # skip version variable
        if len(fname.strip())== 0:
            continue
        with open(webFolder+ '/pages/'+ fname.strip(), 'r') as f:
            res += ('\n' + f.read())
    return Response(res, mimetype='application/javascript')

@app.route('/i18n/<path:path>')
def send_i18n(path):
    file= i18nFolder+ '/'+ path+ '/translation.json'
    # if given language is not found return english
    return send_file(file)

@app.route('/files/<path:path>')
def send_files(path):
    return send_from_directory(webFolder+ '/files', path)

@app.route('/fonts/<path:path>')
def send_webfonts(path):
    return send_from_directory(webFolder+ '/fonts', path)

@app.route('/version')
def get_version():
    res= {}
    try:
        localJs= {}
        with open(webFolder+ '/version.json', 'r') as f:
            localJs= json.loads(f.read())
        res['current']= localJs

        remoteJs= {}
        u= 'https://raw.githubusercontent.com/gokko/beamOfTime/master/bot/version.json'
        with urllib.request.urlopen(u) as url:
            remoteJs = json.loads(url.read().decode())
        res['new']= remoteJs
        updateAvailable= False
        if (remoteJs['version']> localJs['version']):
            updateAvailable= True
        res['update_available']= updateAvailable
    except Exception as ex:
        print("error reading version {0}".format(ex))
    return jsonify(res)

@app.route('/info', methods = ['GET'])
def get_info():
    res= {}
    # if backup exists add backup date
    bkupTime= ''
    file= bkupFolder+ '/info.txt'
    if os.path.isfile(file) and os.path.isfile(bkupFolder+ '/wpa_supplicant.conf') and os.path.isfile(bkupFolder+ '/beamOfTime/bot/app.py'):
        if platform.system() == 'Windows':
            bkupTime= time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(file)))
        else:
            stat = os.stat(file)
            bkupTime= time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

    res['backup_time']= bkupTime

    # add hostname 
    res['hostname']= socket.gethostname()

    # add all ip addresses
    ipaddresses= []
    adapters = ifaddr.get_adapters()
    for adapter in adapters:
        aName= adapter.nice_name.lower()
        for ip in adapter.ips:
            # cleanup adapter names on windows 
            aName= aName.replace('intel(r) ', '')
            aName= aName.replace('dual band wireless-ac', 'wifi')
            aName= aName.replace('ethernet connection ', 'eth')
            # exclude 'internal' adapters
            if type(ip.ip) == str and aName!= 'lo' and not 'virtual' in aName and not 'loopback' in aName and not 'bluetooth' in aName:
                ipaddresses.append({'name': aName, 'ip': ip.ip})
    res['ips']= ipaddresses
    return  jsonify(res)

def get_timedatectl():
    res= {}
    # on raspi read system settings using timedatectl
    if isRaspi:
        p= Popen('timedatectl show', shell=True, stdout=PIPE, close_fds=True)
        for line in p.stdout:
            (key, val)= line.decode('ascii').strip().split('=')
            if key== 'TimeUSec':
                val= val[4:]
            if val== 'no':
                val= False
            if val== 'yes':
                val= True
            res[key]= val
        p= Popen('timedatectl list-timezones', shell=True, stdout=PIPE, close_fds=True)
        tzones= []
        for line in p.stdout:
            tz= line.decode('ascii').strip()
            tzones.append(tz)
        res['timezones']= tzones
    return res

@app.route('/datetime')
def get_datetime():
    res= {}
    # on raspi read system settings using timedatectl
    if isRaspi:
        res= get_timedatectl()

    # on other systems read dummy values from file for debugging
    else:
        with open(webFolder+ '/timedatectl.json') as f:
            res = json.load(f)
        now= datetime.now()
        res['TimeUSec']= now.strftime("%Y-%m-%d %H:%M:%S CET")
        res['timezones']= ['Europe/Berlin', 'Africa/Windhoek', 'America/Adak', 'Antarctica/Vostok', 'Asia/Almaty', 'Indian/Reunion', 'Pacific/Apia']

    return jsonify(res)

@app.route('/datetime', methods = ['POST'])
def send_datetime():
    curSettings= get_timedatectl()

    dtJson= json.loads(request.data)
    # on raspi set timezone and date & time
    if isRaspi:
        Popen('sudo timedatectl set-timezone {0}'.format(dtJson.get('Timezone', 'Europe/Berlin')), shell=True)
        # enable NTP if it's currently disabled
        if dtJson.get('NTP', True) and curSettings.get('NTP', True)== False:
            Popen('sudo timedatectl set-ntp true', shell=True)
        # disable NTP and set given date and time
        elif dtJson.get('NTP', True)== False:
            Popen("sudo timedatectl set-ntp false && sudo date -s '{0}'".format(dtJson.get('TimeUSec', '')), shell=True)
    # on other systems write dummy values to file for debugging
    else:
        with open(webFolder+ '/timedatectl.json', 'w') as f:
            json.dump(dtJson, f)

    return 'OK'

@app.route('/sayIp')
def say_ip():
    ipText= ipToSay= ''
    adapters = ifaddr.get_adapters()
    for adapter in adapters:
        aName= adapter.nice_name.lower()
        if aName== 'lo' or 'virtual' in aName or 'loopback' in aName or 'bluetooth' in aName:
            continue
        for ip in adapter.ips:
            if type(ip.ip) == str:
                ipText= ip.ip
    for i in range(0, len(ipText)):
        ipToSay+= ipText[i]+ ' '
    if isRaspi:
        Popen('espeak -s 30 -g 30 "my i p address is: {0}"'.format(ipToSay), shell=True)
    return ipText

@app.route('/wifi', methods = ['GET'])
def get_wifi():
    wpaConf= ''
    with open(wifiFolder+ '/wpa_supplicant.conf', 'r') as f:
        wpaConf= f.read()
    wifi = WpaSupplicantConf(wpaConf)
    res= wifi.toJsonDict()
    return  jsonify(res)

@app.route('/wifi', methods = ['POST'])
def send_wifi():
    wpaJson= json.loads(request.data)
    # remove ipconf key, as it's for UI only
    wpaJson.pop('ipconf', '')
    wifi = WpaSupplicantConf(wpaJson)
    wpaFilename= wifiFolder+ '/wpa_supplicant.conf'
    wifi.write(wpaFilename)
    # restart wifi service
    if isRaspi:
        Popen('sudo systemctl daemon-reload && sudo systemctl restart dhcpcd', shell=True)
    return 'OK'

@app.route('/update')
def send_update():
    proc= None
    if isRaspi:
        proc= Popen('git -C {0} reset --hard origin/master'.format(rootFolder), stdout=PIPE, stderr=STDOUT, close_fds=True, shell=True)
    else:
        proc= Popen('git -C {0} pull origin master'.format(rootFolder), stdout=PIPE, stderr=STDOUT, close_fds=True, shell=True)
    res= proc.stdout.read()
    proc.stdout.close()
    return res

@app.route('/backup')
def send_backup():
    # skip backup if not running on raspi
    if not isRaspi:
        return 'OK (backup skipped)'
    try:
        if os.path.isdir(bkupFolder):
            shutil.rmtree(bkupFolder, onerror=handleFileError)
        os.mkdir(bkupFolder)
        shutil.copytree(rootFolder, bkupFolder+ '/beamOfTime')
        shutil.copy(wifiFolder+ '/wpa_supplicant.conf', bkupFolder)
        f= open(bkupFolder+ '/info.txt', 'w')
        f.close()
    except OSError as e:
        return 'Error: %s' % e

    return 'OK'

@app.route('/restore')
def send_restore():
    # skip restore if not running on raspi
    if not isRaspi:
        return 'OK (restore skipped)'
    if not os.path.isdir(bkupFolder+ '/beamOfTime') or not os.path.isfile(bkupFolder+ '/beamOfTime/bot/app.py') or not os.path.isfile(bkupFolder+ '/wpa_supplicant.conf'):
        return 'no valid backup found'
    try:
        os.remove(wifiFolder+ '/wpa_supplicant.conf')
        shutil.copy(bkupFolder+ '/wpa_supplicant.conf', wifiFolder)
        shutil.rmtree(rootFolder, onerror=handleFileError)
        shutil.copytree(bkupFolder+ '/beamOfTime', rootFolder)
    except OSError as e:
        return 'Error: %s' % e

    Popen('sudo service bot restart', shell=True)
    return 'OK'

@app.route('/config', methods = ['GET'])
def get_config():
    res= {}
    checkOrRestoreConfigFile()
    with open(os.path.join(clockFolder, configFilename), 'rb') as f:
        res = json.load(f)
    # get current function from clock (may have changed by timer)
    res['settings']['mode']= clock.cfg.get('settings', {}).get('mode', '')
    # get current theme from clock (may have changed by timer)
    if clock.currentTheme.get('name', '')!= '':
        res['settings']['currentTheme']= clock.currentTheme.get('name', '')
    # set default brightness for outer LEDs if not available in config
    if 'ledBrightness2' not in res['system']:
        res['system']['ledBrightness2']= 100
    # get list of animations from clock
    animations= []
    for anim in clock.animations.keys():
        animations.append(anim)
    res['animations']= animations
    # create list of sound files based on sound folder
    sounds= ['cuckoo-hours'] # special cuckoo sound for hours count
    for subdir, dirs, files in os.walk(soundsFolder):
        # skip special folder for cuckoo hours sound
        subfolder= os.path.basename(subdir)
        if subfolder== 'cuckoo-hours':
            continue
        for file in files:
            sounds.append(subfolder+ '/'+ file)
    res['sounds']= sounds

    # create list of available languages based on locales folder content
    languages= []
    for subdir, dirs, files in os.walk(i18nFolder):
        for dir in dirs:
            langCode = dir
            langName= ''
            langDir= 'ltr'
            file= i18nFolder+ '/'+ dir+ '/translation.json'
            if not os.path.isfile(file):
                continue
            with open(file, 'rb') as f:
                js = json.load(f)
                if js and js['main']:
                    langName= js['main']['language_name']
                    langDir= js['main']['language_dir']

            languages.append({"value": langCode, "text": langName, "dir": langDir})

    res['languages']= languages
    return jsonify(res)

@app.route('/config', methods = ['POST'])
def send_config():
    confJs= json.loads(request.data)
    # remove the langauges part from config, it will be recreated on request based on the locales folder content
    confJs.pop('languages', '')
    # remove the sounds part from config, it will be recreated on request based on the sounds folder content
    confJs.pop('sounds', '')
    # update config in clock app
    clock.updateConfig(confJs)
    # write config to file
    conf = json.dumps(confJs, indent=4, ensure_ascii=False).encode('utf8')
    tmpFile = os.path.join(clockFolder, 'config-new.json')
    confFile = os.path.join(clockFolder, configFilename)
    # create temporary file
    with open(tmpFile, 'wb') as f:
        f.write(conf)
    # delete old file and rename new when done writing
    if os.path.exists(confFile):
        os.remove(confFile)
    os.rename(tmpFile, confFile)
    return 'OK'

@app.route('/restart/<path:path>')
def send_restart(path):
    # skip restart if not running on raspi
    if not isRaspi:
        return 'OK (restart skipped)'

    # client is not expecting any result, as service can't responde due to restart
    if (path.lower() == 'reboot'):
        Popen('sudo reboot', shell=True)
    elif (path.lower() == 'restart'):
        Popen('sudo service bot restart', shell=True)
    elif (path.lower() == 'shutdown'):
        Popen('sudo shutdown now', shell=True)
    return path+ ' OK'


# handle exit request
def sigterm_handler(_signo, _stack_frame):
    print("bot clock service is going to stop")
    clock.stop()
    #os._exit(0)


if __name__ == '__main__':
    # handle SIGTERM to gracefully stop clock
    signal.signal(signal.SIGTERM, sigterm_handler)

    # if config file doesn't exist, copy from backup or from setup
    checkOrRestoreConfigFile()

    clock = BotClock()

    app.config['JSON_AS_ASCII'] = False
    port= 80
    if not isRaspi:
        port= 8080
    t = Thread(target=app.run, args=('0.0.0.0', port, False))
    t.start()
    
    clock.run()

    
