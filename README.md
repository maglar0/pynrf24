
nrf24
=====

Python module for the nRF24L01+ tansceiver chip from Nordic Semiconductor.
Written by Lars Magnusson and placed in the public domain. Note however
that it also contains some documentation from the nRF24L01+ Product 
Specification, which is copyright by Nordic Semiconductor.

This module is very low level and does not contain ready-to-use functions
for setting everything up and start transferring things. Instead, it 
focuses on flexibility and making it very, very easy to experiment. You 
should make extensive use of the
[nRF24L01+ Product Specification](http://www.nordicsemi.com/eng/content/download/2726/34069/file/nRF24L01P_Product_Specification_1_0.pdf) 
to accomplish what you want to do. You might also want to check out this
[tutorial](http://www.diyembedded.com/tutorials/nrf24l01_0/nrf24l01_tutorial_0.pdf).

Raspberry Pi is the intended platform, but the module should be adaptable
to other environments as well. You just need to provide one function for
SPI communication and a way to set the CE (chip enable) pin high and low,
and optionally a way to trigger a callback on a high-to-low or simply 
low IRQ pin on the nRF24L01+ chip.


Usage
-----

The registers of the nRF24L01+ are present in this module with the 
prefix REG_. So for example REG_STATUS is the STATUS register and it 
can be read by

    status = device.get(REG_STATUS)

(where device is an instance of the class NRF24Device). status is now an
8 bit int. Individual bit fields in the registers are present in the
module with the same names as in the nRF24L01+ documentation. You can 
access individual bit fields (hereafter referred to as fields or register 
fields) by doing

    TX_FULL.get(status)

which will return the int 0 or 1 depending on the TX_FULL bit in status.
An easier way is to just do

    tx_full = device.get(TX_FULL)

You can also read many fields and registers at the same time, e.g.

    a, b, c, d = device.get(TX_FULL, RX_EMPTY, REG_RX_ADDR_P5, RF_CH)

To write registers and fields you use the set() function, like this:

    device.set(RF_CH(40), REG_RX_ADDR_P0([0x6b, 0x6b, 0x6b, 0x6b, 0x6b]))


To write and read the actual packet payload use the functions
write_tx_payload() and read_rx_payload().


Thread Safety
-------------

This module is not designed with threading in mind except one thing,
the ability to wait for interrupts and to cancel a wait on another
thread. Look at the documentation for the functions wait_for_irq_low(),
wait_for_irq_low_cancellable(), and cancel_wait_for_irq() as well as 
the example program using those.

You may use the module from multiple threads but you may not use
a single object, e.g. an instance of NRF24Device, from more than one 
thread at once, with one exception. The method cancel_wait_for_irq() is
to be called from another thread. Do note however that the resources 
used by NRF24Device, i.e. the SPI object and GPIO object, might **not**
be thread safe and thus using two NRF24Devices from two different
threads might not be safe. I don't know how that works on Raspberry
Pi, but I suggest you do protect all calls into this module and
all other code using GPIO or SPI with a single mutex of some kind if
you use many threads.


Performance
-----------

The performance is awful. Don't expect more than 250-500 kbit/s or
so on a Raspberry Pi.


Examples
--------

There are several examples in the example directory. Below are two
very basic ones.

The examples below use
 * raspberry-gpio-python: http://sourceforge.net/projects/raspberry-gpio-python/
 * py-spidev: https://github.com/doceme/py-spidev

Example 1: wait for 1 byte packets and print them.

    # CE connected to GPIO 17 aka pin 11,
    # CSN connected to CE0 aka GPIO 8 aka pin 24
    # Other things connected in the obvious way, MISO to MISO etc.

    from nrf24 import *
    import RPi.GPIO as GPIO
    import spidev
    import time

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(17, GPIO.OUT)

    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 10*1000*1000
    device = NRF24Device(spi, NRF24Gpio(17))
    device.reset_to_default()
    PACKET_SIZE = 1
    device.set(PRIM_RX(1), PWR_UP(1), RX_PW_P0(PACKET_SIZE))
    # Need to wait Tpd2stby until we are in Standby-I mode
    time.sleep(0.01)
    device.chip_enable_high()

    while True:
        while device.get(RX_EMPTY):
            pass
        status, payload = device.read_rx_payload(PACKET_SIZE)
        print payload
 

Example 2: send a 1 byte packet containing 42.

    # CE connected to GPIO 25 aka pin 22,
    # CSN connected to CE1 aka GPIO 7 aka pin 26

    from nrf24 import *
    import RPi.GPIO as GPIO
    import spidev
    import time

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(25, GPIO.OUT)

    spi = spidev.SpiDev()
    spi.open(0, 1)
    spi.max_speed_hz = 10*1000*1000
    device = NRF24Device(spi, NRF24Gpio(25))
    device.reset_to_default()
    device.set(PWR_UP(1))
    device.write_tx_payload([42])
    time.sleep(0.01)
    device.chip_enable_high()
    time.sleep(0.001)
    device.chip_enable_low()

