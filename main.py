import network
import espnow
import esp32
import time
import json

import uasyncio as asyncio
import binascii
from machine import Pin, deepsleep, reset
#from machine import Encoder as MENC
from primitives import Pushbutton
from primitives import Encoder
from rotary_irq_esp import RotaryIRQ


config = []
with open("settings.json") as f:
    config = json.load(f)
HOST = config["host"]
SLEEP = config["sleep"]
DEBUG = config["debug"]
ENCSTEPS = config["encsteps"]
ALTSTEPS = config["altsteps"]
ACCESSPOINT = None
PASSWORD = None
SPECIAL = False

VFDVAL = 0

# A WLAN interface must be active to send()/recv()
sta = network.WLAN(network.STA_IF)  # Or network.AP_IF
sta.active(True)
sta.disconnect()

e = espnow.ESPNow()
e.active(True)

if HOST:
    peer = binascii.unhexlify(HOST.replace(':', ''))
    #peer = b'|\x87\xce\xcbF\xac'   # MAC address of peer's wifi interface
    central_control = b"H'\xe2N3("
    e.add_peer(peer)      # Must add_peer() before send()
    e.add_peer(central_control)
    e.send(peer, "C") #sending connection

def encoder_updated(pos, delta):
    #print(pos*5)
    if not MODE:
        send_message(pos)

#local encoder
r = RotaryIRQ(pin_num_clk=16, pin_num_dt=18, incr=ENCSTEPS, min_val=0, max_val=255, pull_up=True, range_mode=RotaryIRQ.RANGE_BOUNDED)
#py = Pin(16, Pin.IN, Pin.PULL_UP)
#px = Pin(18, Pin.IN, Pin.PULL_UP)
#enc = Encoder(px, py, vmin=0, vmax=51, div=4, callback=encoder_updated)
led = Pin(15, Pin.OUT)
led.on()

phase_a = Pin(7, Pin.IN, Pin.PULL_UP)
phase_b = Pin(5, Pin.IN, Pin.PULL_UP)

t1 = Pushbutton(Pin(17, Pin.IN, Pin.PULL_UP), suppress=True)

#globals
MODE = 0
LASTMESSAGE = time.ticks_ms()
ENCODER_COUNTS = 0
esp32.wake_on_ext0(pin = Pin(17), level = esp32.WAKEUP_ALL_LOW)


def update_setting(msgd):
    global SLEEP, DEBUG, HOST, ENCSTEPS, ALTSTEPS, ACCESSPOINT, PASSWORD, config
    if msgd[1:3] == "HM":
        HOST = msgd[3:]
        peer = binascii.unhexlify(HOST.replace(':', ''))
        if not config["host"]:
            config["host"] = HOST
            config["sleep"] = SLEEP
            config["debug"] = DEBUG
            with open("settings.json","w") as jsonfile:
                json.dump(config, jsonfile)
            reset()
        else:
            e.send(peer, "C", False)
            return
    if msgd[1:3] == "RT":
        SLEEP = int(msgd[3:])
    if msgd[1:3] == "RD":
        DEBUG = msgd[3:]
    if msgd[1:3] == "ES":
        ENCSTEPS = int(msgd[3:])
        r.set(incr=ENCSTEPS)
    if msgd[1:3] == "AS":
        ALTSTEPS = int(msgd[3:])
    if msgd[1:3] == "AP":
        ACCESSPOINT = msgd[3:]
    if msgd[1:3] == "PW":
        PASSWORD = msgd[3:]
    print(ENCSTEPS, ALTSTEPS)

def network_update():
    import network, gc, time, uota
    asyncio.new_event_loop()
    #gc.collect()
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    #wlan.config(dhcp_hostname="lathe-test")
    wlan.config(dhcp_hostname="lathe")
    endtime = time.ticks_add(time.ticks_ms(), 45000)
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.connect(ACCESSPOINT, PASSWORD)
        while not wlan.isconnected() and time.ticks_diff(endtime, time.ticks_ms()) > 0:
            pass
    print('network config:', wlan.ifconfig())
    if uota.check_for_updates():
        print("Found new update....Downloading")
        time.sleep(5)
        reset()
    print("No update found")
    time.sleep(5)
    reset()
    
    return wlan.isconnected()

#SPECIAL
def start_encoder_mode():
    global enc, MODE
    print("starting encoder mode")
    MODE = 1
    enc = Encoder(phase_a, phase_b, div=4)
    asyncio.create_task(send_distance())
#SPECIAL
def stop_encoder_mode():
    print("Stopping encoder mode")
    send_message("E")
    time.sleep(10)
    #reset()
#SPECIAL
def reset_distance():
    global enc
    print("Resetting encoder distance")
    enc = Encoder(phase_a, phase_b, div=4)
    ENCODER_COUNTS = 0
    #do encoder reset

def t1_press():
    global t1
    print("press")
    send_message("F")

def t1_double():
    if not MODE and SPECIAL:
        start_encoder_mode()
        send_message("E")
    if MODE:
        stop_encoder_mode()
    else:
        send_message("F")
    
def t1_long():
    global t1
    print("long")
    if MODE:
        reset_distance()
    send_message("R")

def send_message(msg):
    global LASTMESSAGE
    if not HOST:
        return
    e.send(peer, "{}".format(msg), False)
    if DEBUG:
        e.send(central_control, "{}".format(msg), False) 
    if msg != 'A': #don't have ping response count as lastmessage    
        LASTMESSAGE = time.ticks_ms()
#SPECIAL    
async def send_distance():
    global ENCODER_COUNTS
    while True:
        v = enc.value()
        if ENCODER_COUNTS != v:
            counts = "V{}".format(v)
            ENCODER_COUNTS = v
            send_message(counts)
        await asyncio.sleep_ms(100)

async def get_message():
    global LASTMESSAGE
    while True:
        host, msg = e.recv(timeout_ms=10)
        if msg and not MODE:
            print(host, msg)
            msgd = msg.decode("utf-8")
            if msgd.startswith('U'):
                update_setting(msgd)
            elif msgd.startswith('R'):
                print("starts with R")
            elif msgd.startswith('P'):
                send_message('A')
            else:
                reset_encoder(int(msg))
            if not msgd.startswith('P'):    
                LASTMESSAGE = time.ticks_ms()
        await asyncio.sleep_ms(50)

def reset_encoder(msg):
    global VFDVAL,r
    VFDVAL = msg
    r.set(msg)

next_on = time.ticks_ms() + 5000

async def toggle_led():
    global next_on
    while True:
        if MODE:
            ledtime = 1000
        else:
            ledtime = 5000

        tv = time.ticks_ms()
        #deepsleep here
        if time.ticks_diff(tv, LASTMESSAGE) > SLEEP*60000:
            send_message("D")
            deepsleep()
        if time.ticks_diff(next_on, tv) <= 0:
            led.on()
            next_on = time.ticks_ms() + ledtime
        if time.ticks_diff(next_on, tv) <= (ledtime-200):
            led.off()
        await asyncio.sleep_ms(50)

async def main():
    global t1, VFDVAL
    t1.release_func(t1_press, ())
    t1.double_func(t1_double, ())
    t1.long_func(t1_long, ())
    asyncio.create_task(get_message())
    asyncio.create_task(toggle_led())
    while True:
        val_new = r.value()
        if val_new != VFDVAL:
            if t1.rawstate():
                t1._ld.stop()
                r.set(incr=ALTSTEPS)
            else:
                r.set(incr=ENCSTEPS)    
            VFDVAL = val_new
            send_message(VFDVAL)
        await asyncio.sleep_ms(25)
    
if __name__ == '__main__':
    gc.enable()
    asyncio.run(main())
