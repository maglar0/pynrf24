"""
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
"""

# Use this Python code to copy the above comment into README.md:
# python -c "import nrf24; print nrf24.__doc__" > README.md

import collections
import copy
import sys
import threading
import time

try:
    import RPi.GPIO as GPIO
except:
    # It's ok, but can't use NRF24Gpio class. The user would have to provide
    # another class to handle CE and optionally IRQ pins.
    pass




_SPI_NOP = 0xFF


_RegisterFieldInfo = collections.namedtuple("_RegisterFieldInfo", "name start_bit num_bits reset_value rw description")
_RegisterInfo = collections.namedtuple("_RegisterInfo", "name address min_size max_size fields description")


_REGISTERS = [
    _RegisterInfo("CONFIG", 0x00, 1, 1, [
            _RegisterFieldInfo("Reserved", 7, 1, 0, "R/W", "Only '0' allowed"),
            _RegisterFieldInfo("MASK_RX_DR", 6, 1, 0, "R/W", "Mask interrupt caused by RX_DR\n1: Interrupt not reflected on the IRQ pin\n0: Reflect RX_DR as active low interrupt on the IRQ pin"),
            _RegisterFieldInfo("MASK_TX_DS", 5, 1, 0, "R/W", "Mask interrupt caused by TX_DS\n1: Interrupt not reflected on the IRQ pin\n0: Reflect TX_DS as active low interrupt on the IRQ pin"),
            _RegisterFieldInfo("MASK_MAX_RT", 4, 1, 0, "R/W", "Mask interrupt caused by MAX_RT\n1: Interrupt not reflected on the IRQ pin\n0: Reflect MAX_RT as active low interrupt on the IRQ pin"),
            _RegisterFieldInfo("EN_CRC", 3, 1, 1, "R/W", "Enable CRC. Forced high if one of the bits in the EN_AA is high"),
            _RegisterFieldInfo("CRCO", 2, 1, 0, "R/W", "CRC encoding scheme\n'0' - 1 byte\n'1' - 2 bytes"),
            _RegisterFieldInfo("PWR_UP", 1, 1, 0, "R/W", "1: POWER UP, 0: POWER DOWN"),
            _RegisterFieldInfo("PRIM_RX", 0, 1, 0, "R/W", "RX/TX control\n1: PRX, 0: PTX")
        ],
        "Configuration Register"),
        
    _RegisterInfo("EN_AA", 0x01, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("ENAA_P5", 5, 1, 1, "R/W", "Enable auto acknowledgement data pipe 5"),
            _RegisterFieldInfo("ENAA_P4", 4, 1, 1, "R/W", "Enable auto acknowledgement data pipe 4"),
            _RegisterFieldInfo("ENAA_P3", 3, 1, 1, "R/W", "Enable auto acknowledgement data pipe 3"),
            _RegisterFieldInfo("ENAA_P2", 2, 1, 1, "R/W", "Enable auto acknowledgement data pipe 2"),
            _RegisterFieldInfo("ENAA_P1", 1, 1, 1, "R/W", "Enable auto acknowledgement data pipe 1"),
            _RegisterFieldInfo("ENAA_P0", 0, 1, 1, "R/W", "Enable auto acknowledgement data pipe 0"),
        ],
        "Enable 'Auto Acknowledgment' Function Disable this functionality to be compatible with nRF2401, see page 75"),

    _RegisterInfo("EN_RXADDR", 0x02, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("ERX_P5", 5, 1, 0, "R/W", "Enable data pipe 5."),
            _RegisterFieldInfo("ERX_P4", 4, 1, 0, "R/W", "Enable data pipe 4."),
            _RegisterFieldInfo("ERX_P3", 3, 1, 0, "R/W", "Enable data pipe 3."),
            _RegisterFieldInfo("ERX_P2", 2, 1, 0, "R/W", "Enable data pipe 2."),
            _RegisterFieldInfo("ERX_P1", 1, 1, 1, "R/W", "Enable data pipe 1."),
            _RegisterFieldInfo("ERX_P0", 0, 1, 1, "R/W", "Enable data pipe 0.")
        ],
        "Enabled RX Addresses"),
        
    _RegisterInfo("SETUP_AW", 0x03, 1, 1, [
            _RegisterFieldInfo("Reserved", 2, 6, 0, "R/W", "Only '000000' allowed"),
            _RegisterFieldInfo("AW", 0, 2, 0b11, "R/W", """RX/TX Address field width
'00' - Illegal
'01' - 3 bytes
'10' - 4 bytes
'11' - 5 bytes
LSByte is used if address width is below 5 bytes""")
        ],
        "Setup of Address Widths (common for all data pipes)"),
        
    _RegisterInfo("SETUP_RETR", 0x04, 1, 1, [
            _RegisterFieldInfo("ARD", 4, 4, 0, "R/W", """Auto Retransmit Delay 
'0000' - Wait 250uS
'0001' - Wait 500uS
'0010' - Wait 750uS
........
'1111' - Wait 4000uS
(Delay defined from end of transmission to start of
next transmission)"""),
            _RegisterFieldInfo("ARC", 0, 4, 0b0011, "R/W", """Auto Retransmit Count
'0000' - Re-Transmit disabled
'0001' - Up to 1 Re-Transmit on fail of AA
......
'1111' - Up to 15 Re-Transmit on fail of AA""")
        ],
        "Setup of Automatic Retransmission"),
 
    _RegisterInfo("RF_CH", 0x05, 1, 1, [
            _RegisterFieldInfo("Reserved", 7, 1, 0, "R/W", "Only '0' allowed"),
            _RegisterFieldInfo("RF_CH", 0, 7, 0b0000010, "R/W", "Sets the frequency channel nRF24L01+ operates on")
        ],
        "RF Channel"),
        
    _RegisterInfo("RF_SETUP", 0x06, 1, 1, [
            _RegisterFieldInfo("CONT_WAVE", 7, 1, 0, "R/W", "Enables continuous carrier transmit when high."),
            _RegisterFieldInfo("Reserved", 6, 1, 0, "R/W", "Only '0' allowed"),
            _RegisterFieldInfo("RF_DR_LOW", 5, 1, 0, "R/W", "Set RF Data Rate to 250kbps. See RF_DR_HIGH for encoding."),
            _RegisterFieldInfo("PLL_LOCK", 4, 1, 0, "R/W", "Force PLL lock signal. Only used in test"),
            _RegisterFieldInfo("RF_DR_HIGH", 3, 1, 1, "R/W", """Select between the high speed data rates. This bit is don't care if RF_DR_LOW is set.
Encoding:
[RF_DR_LOW, RF_DR_HIGH]:
'00' - 1Mbps
'01' - 2Mbps
'10' - 250kbps
'11' - Reserved)"""),
            _RegisterFieldInfo("RF_PWR", 1, 2, 0b11, "R/W", """Set RF output power in TX mode 
'00' - -18dBm
'01' - -12dBm
'10' - -6dBm
'11' - 0dBm"""),
            _RegisterFieldInfo("Obsolete", 0, 1, 0, "R/W", """Don't care (undefined reset value)"""),
        ],
        "RF Setup Register"),
        
    _RegisterInfo("STATUS", 0x07, 1, 1, [
            _RegisterFieldInfo("Reserved", 7, 1, 0, "R/W", "Only '0' allowed"),
            _RegisterFieldInfo("RX_DR", 6, 1, 0, "R/W", "Data Ready RX FIFO interrupt. Asserted when new data arrives RX FIFOc. Write 1 to clear bit."),
            _RegisterFieldInfo("TX_DS", 5, 1, 0, "R/W", "Data Sent TX FIFO interrupt. Asserted when packet transmitted on TX. If AUTO_ACK is activated, this bit is set high only when ACK is received.\nWrite 1 to clear bit."),
            _RegisterFieldInfo("MAX_RT", 4, 1, 0, "R/W", "Maximum number of TX retransmits interrupt Write 1 to clear bit. If MAX_RT is asserted it must be cleared to enable further communication."),
            _RegisterFieldInfo("RX_P_NO", 1, 3, 0b111, "R", "Data pipe number for the payload available for reading from RX_FIFO\n000-101: Data Pipe Number\n110: Not Used\n111: RX FIFO Empty"),
            _RegisterFieldInfo("TX_FULL", 0, 1, 0, "R", "TX FIFO full flag.\n1: TX FIFO full.\n0: Available locations in TX FIFO.")
        ],
        "Status Register (In parallel to the SPI command word applied on the MOSI pin, the STATUS register is shifted serially out on the MISO pin)"),

    _RegisterInfo("OBSERVE_TX", 0x08, 1, 1, [
            _RegisterFieldInfo("PLOS_CNT", 4, 4, 0, "R", "Count lost packets. The counter is overflow protected to 15, and discontinues at max until reset. The counter is reset by writing to RF_CH. See page 75."),
            _RegisterFieldInfo("ARC_CNT", 0, 4, 0, "R", "Count retransmitted packets. The counter is reset when transmission of a new packet starts. See page 75.")
        ],
        "Transmit observe register"),

    _RegisterInfo("RPD", 0x09, 1, 1, [
            _RegisterFieldInfo("Reserved", 1, 7, 0, "R", ""),
            _RegisterFieldInfo("RPD", 0, 1, 0, "R", "Received Power Detector. This register is called CD (Carrier Detect) in the nRF24L01. The name is different in nRF24L01+ due to the different input power level threshold for this bit. See section 6.4 on page 25.")
        ],
        "Received Power Detector"),
        
    _RegisterInfo("RX_ADDR_P0", 0x0A, 3, 5, [],
        "Receive address data pipe 0. 5 Bytes maximum length. (LSByte is written first. Write the number of bytes defined by SETUP_AW)"),

    _RegisterInfo("RX_ADDR_P1", 0x0B, 3, 5, [],
        "Receive address data pipe 1. 5 Bytes maximum length. (LSByte is written first. Write the number of bytes defined by SETUP_AW)"),
        
    _RegisterInfo("RX_ADDR_P2", 0x0C, 1, 1, [
            _RegisterFieldInfo("RX_ADDR_P2", 0, 8, 0xC3, "R/W", "Receive address data pipe 2. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]")
        ],
        "Receive address data pipe 2. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]"),

    _RegisterInfo("RX_ADDR_P3", 0x0D, 1, 1, [
            _RegisterFieldInfo("RX_ADDR_P3", 0, 8, 0xC4, "R/W", "Receive address data pipe 3. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]")
        ],
        "Receive address data pipe 3. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]"),

    _RegisterInfo("RX_ADDR_P4", 0x0E, 1, 1, [
            _RegisterFieldInfo("RX_ADDR_P4", 0, 8, 0xC5, "R/W", "Receive address data pipe 4. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]")
        ],
        "Receive address data pipe 4. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]"),

    _RegisterInfo("RX_ADDR_P5", 0x0F, 1, 1, [
            _RegisterFieldInfo("RX_ADDR_P5", 0, 8, 0xC6, "R/W", "Receive address data pipe 5. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]")
        ],
        "Receive address data pipe 5. Only LSB. MSBytes are equal to RX_ADDR_P1[39:8]"),
        
    _RegisterInfo("TX_ADDR", 0x10, 3, 5, [],
        """Transmit address. Used for a PTX device only. (LSByte is written first)
Set RX_ADDR_P0 equal to this address to handle automatic acknowledge if this is a PTX device with Enhanced ShockBurstTM enabled. See page 75."""),

    _RegisterInfo("RX_PW_P0", 0x11, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("RX_PW_P0", 0, 6, 0, "R/W", """Number of bytes in RX payload in data pipe 0 (1 to 32 bytes).
0 Pipe not used
1 = 1 byte
...
32 = 32 bytes""")
        ],
        "RX payload size pipe 0"),

    _RegisterInfo("RX_PW_P1", 0x12, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("RX_PW_P1", 0, 6, 0, "R/W", """Number of bytes in RX payload in data pipe 1 (1 to 32 bytes).
0 Pipe not used
1 = 1 byte
...
32 = 32 bytes""")
        ],
        "RX payload size pipe 1"),

    _RegisterInfo("RX_PW_P2", 0x13, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("RX_PW_P2", 0, 6, 0, "R/W", """Number of bytes in RX payload in data pipe 2 (1 to 32 bytes).
0 Pipe not used
1 = 1 byte
...
32 = 32 bytes""")
        ],
        "RX payload size pipe 2"),

    _RegisterInfo("RX_PW_P3", 0x14, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("RX_PW_P3", 0, 6, 0, "R/W", """Number of bytes in RX payload in data pipe 3 (1 to 32 bytes).
0 Pipe not used
1 = 1 byte
...
32 = 32 bytes""")
        ],
        "RX payload size pipe 3"),

    _RegisterInfo("RX_PW_P4", 0x15, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("RX_PW_P4", 0, 6, 0, "R/W", """Number of bytes in RX payload in data pipe 4 (1 to 32 bytes).
0 Pipe not used
1 = 1 byte
...
32 = 32 bytes""")
        ],
        "RX payload size pipe 4"),

    _RegisterInfo("RX_PW_P5", 0x16, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("RX_PW_P5", 0, 6, 0, "R/W", """Number of bytes in RX payload in data pipe 5 (1 to 32 bytes).
0 Pipe not used
1 = 1 byte
...
32 = 32 bytes""")
        ],
        "RX payload size pipe 5"),

    _RegisterInfo("FIFO_STATUS", 0x17, 1, 1, [
            _RegisterFieldInfo("Reserved", 7, 1, 0, "R/W", "Only '0' allowed"),
            _RegisterFieldInfo("TX_REUSE", 6, 1, 0, "R", """Used for a PTX device. Pulse the rfce high for at least 10us to Reuse last transmitted payload. TX payload reuse is active until W_TX_PAYLOAD or FLUSH TX is executed. TX_REUSE is set by the SPI command REUSE_TX_PL, and is reset by the SPI commands W_TX_PAYLOAD or FLUSH TX"""),
            _RegisterFieldInfo("TX_FULL_", 5, 1, 0, "R", "TX FIFO full flag. 1: TX FIFO full. 0: Available locations in TX FIFO."""), # TX_FULL already defined in STATUS, so added an underscore suffix
            _RegisterFieldInfo("TX_EMPTY", 4, 1, 1, "R", "TX FIFO empty flag. 1: TX FIFO empty. 0: Data in TX FIFO."),
            _RegisterFieldInfo("Reserved", 2, 2, 0, "R/W", "Only '00' allowed"),
            _RegisterFieldInfo("RX_FULL", 1, 1, 0, "R", "RX FIFO full flag. 1: RX FIFO full. 0: Available locations in RX FIFO."),
            _RegisterFieldInfo("RX_EMPTY", 0, 1, 1, "R", "RX FIFO empty flag. 1: RX FIFO empty. 0: Data in RX FIFO.")
        ],
        "FIFO Status Register"),

    _RegisterInfo("DYNPD", 0x1C, 1, 1, [
            _RegisterFieldInfo("Reserved", 6, 2, 0, "R/W", "Only '00 allowed"),
            _RegisterFieldInfo("DPL_P5", 5, 1, 0, "R/W", "Enable dynamic payload length data pipe 5. (Requires EN_DPL and ENAA_P5)"),
            _RegisterFieldInfo("DPL_P4", 4, 1, 0, "R/W", "Enable dynamic payload length data pipe 4. (Requires EN_DPL and ENAA_P4)"),
            _RegisterFieldInfo("DPL_P3", 3, 1, 0, "R/W", "Enable dynamic payload length data pipe 3. (Requires EN_DPL and ENAA_P3)"),
            _RegisterFieldInfo("DPL_P2", 2, 1, 0, "R/W", "Enable dynamic payload length data pipe 2. (Requires EN_DPL and ENAA_P2)"),
            _RegisterFieldInfo("DPL_P1", 1, 1, 0, "R/W", "Enable dynamic payload length data pipe 1. (Requires EN_DPL and ENAA_P1)"),
            _RegisterFieldInfo("DPL_P0", 0, 1, 0, "R/W", "Enable dynamic payload length data pipe 0. (Requires EN_DPL and ENAA_P0)")
        ],
        "Enable dynamic payload length"),

    _RegisterInfo("FEATURE", 0x1D, 1, 1, [
            _RegisterFieldInfo("Reserved", 3, 5, 0, "R/W", "Only '00000' allowed"),
            _RegisterFieldInfo("EN_DPL", 2, 1, 0, "R/W", "Enables Dynamic Payload Length"),
            _RegisterFieldInfo("EN_ACK_PAY", 1, 1, 0, "R/W", "Enables Payload with ACK"),
            _RegisterFieldInfo("EN_DYN_ACK", 0, 1, 0, "R/W", "Enables the W_TX_PAYLOAD_NOACK command")
        ],
        "Feature Register")
]


def _verify_registers():
    "Quick check for some inconsitencies in REGISTERS"
    register_addresses = set()
    register_names = set()
    for register in _REGISTERS:
        assert register.name not in register_names
        register_names.add(register.name)
        
        assert register.address not in register_addresses
        register_addresses.add(register.address)
        
        bits = set()
        for field in register.fields:
            for bit in range(field.start_bit, field.start_bit + field.num_bits):
                assert bit not in bits, "Bit %d in %s overlaps" % (bit, field.name)
                bits.add(bit)
        assert bits == set(range(0, 8)) or bits == set(), "Bits in %s:%s is not 0:7 or empty but %r" % (register.name, field.name, bits)

_verify_registers()



def _get_unshifted_mask(field):
    assert isinstance(field, _RegisterField) or issubclass(field, _RegisterField)
    unshifted_mask = (1 << field.NUM_BITS) - 1
    return unshifted_mask


def _get_mask(field):
    unshifted_mask = _get_unshifted_mask(field)
    shifted_mask = unshifted_mask << field.START_BIT
    return shifted_mask




class _RegisterFieldSet(object):
    """Represents the value of several fields in one register. Created with the | operator,
    similar to how to OR integers together. 
    Example: PWR_UP(1) | PRIM_RX(1)
    """
    
    def __init__(self, field_or_fieldset=None):
        if field_or_fieldset is None:
            self.fields = []
        elif isinstance(field_or_fieldset, _RegisterField):
            self.fields = [field_or_fieldset]
        else:
            assert isinstance(field_or_fieldset, _RegisterFieldSet)
            self.fields = copy.copy(field_or_fieldset.fields)

    def get_register_address(self):
        assert len(self.fields) >= 1
        address = self.fields[0].REGISTER_ADDRESS
        return address
    
    def get_register_name(self):
        assert len(self.fields) >= 1
        address = self.fields[0].REGISTER_NAME
        return address

    def get_mask(self):
        mask = 0
        for f in self.fields:
            m = _get_mask(f)
            assert mask & m == 0
            mask |= m
        assert 0 <= mask <= 0xFF
        return mask

    def get_value(self):
        value = 0
        for f in self.fields:
            v = f.get_unshifted_value()
            assert 0 <= value <= 0xFF
            value |= v
        assert (value & self.get_mask()) == value
        return value
        
    def get_num_fields(self):
        return len(self.fields)

    def __ior__(self, other):
        if isinstance(other, _RegisterField):
            assert self.fields == [] or other.REGISTER_ADDRESS == self.get_register_address(), (
                "%s and %s are in different registers" % (
                        other.REGISTER_NAME, self.get_register_name()))
            assert all(other.FIELD_NAME != f.FIELD_NAME for f in self.fields), (
                "%s already included" % other.FIELD_NAME)
            self.fields.append(other)
        else:
            assert isinstance(other, _RegisterFieldSet)
            for f in other.fields:
                self |= f
        return self

    def __or__(self, other):
        r = _RegisterFieldSet(self)
        r |= other
        return r

    def __str__(self):
        return repr(self)

    def __repr__(self):
        return " | ".join(repr(f) for f in self.fields) or "_RegisterFieldSet()"


class _RegisterField(object):
    """One subclass of this class will be created for each field in each variable and added
    to this Python module to be used directly in code. E.g. PWR_UP will be a subclass and it 
    will contain information about the PWR_UP field, like bit position, description etc.
    """

    def __init__(self, value=None):
        assert value is None or "W" in self.RW, (
                "Can't write to register %s:%s" % (self.REGISTER_NAME, self.FIELD_NAME))
        assert value is None or 0 <= value <= self.get_max_value(), (
                "%d is out of range for register %s:%s" % (value, self.REGISTER_NAME, self.field_name))
        self.value = value

    def get_unshifted_value(self):
        """Returns the value not shifted down to lowest bits. 
        Example: RX_P_NO(7).get_unshifted_value() == 14 # because RX_P_NO starts at bit 1.
        """
        value = self.value << self.START_BIT
        assert value & _get_mask(self) == value, "%r, mask %r" % (self, _get_mask(self))
        return value

    @classmethod
    def get_max_value(cls):
        """Return the maximum possible value to store in this register field.
        Example: RX_P_NO.get_max_value() # Returns 7 because RX_P_NO is a 3 bit field.
        """
        return (1 << cls.NUM_BITS) - 1

    @classmethod
    def get(cls, x):
        """Return the value of this field if the register that this field belongs to is x.
        Example: TX_DS(0x21) == 1   # 0x21 = 0b00100001 so bit 5 (which is TX_DS) is 1.
        """
        assert 0 <= x == int(x) <= 255
        return ((x >> cls.START_BIT) & _get_unshifted_mask(cls))

    def __or__(self, other):
        return _RegisterFieldSet(self) | other

    def __str__(self):
        return repr(self)

    def __repr__(self):
        return "%s(%d)" % (self.FIELD_NAME, self.value)

    # Define thse static constants in subclasses:
    # REGISTER_NAME - e.g. "STATUS"
    # REGISTER_ADDRESS - e.g. 0x07
    # FIELD_NAME - e.g.
    # START_BIT
    # NUM_BITS
    # RESET_VALUE
    # RW
    # DESCRIPTION


class _Register(object):
    """Each subclass represents one register in nRF24L01+. E.g. REG_STATUS
    represents the STATUS register and contains address, size, fields, etc
    """

    def __init__(self, value=None):
        assert (value is None or
                self.MIN_SIZE == self.MAX_SIZE == 1 and
                        0 <= value == int(value) <= 255 or
                self.MIN_SIZE > 1 and
                        isinstance(value, list) and
                        self.MIN_SIZE <= len(value) <= self.MAX_SIZE and
                        all(0 <= v == int(v) <= 255 for v in value)), (
                "Invalid value %r for register %s" % (value, self.NAME))
        self.value = value

    def __str__(self):
        return repr(self)

    def __repr__(self):
        return "%s(%d)" % (self.NAME, self.value)

    # Define these static constants in subclasses
    # NAME - e.g. "STATUS"
    # ADDRESS - e.g. 0x07
    # MIN_SIZE - minimum number of bytes in the register, e.g. 1
    # MAX_SIZE - e.g. 1
    # FIELDS - tuple with _RegisterField subclasses
    # DESCRIPTION - string describing the register



def _create_field_class(register_name, register_address, field_name, start_bit, num_bits,
                        reset_value, rw, description):
    assert 0 <= start_bit <= 7
    assert 1 <= start_bit+num_bits <= 8
    assert rw in ("R", "W", "R/W"), rw
    
    docstring = "%s (bit %s) in register %s (address 0x%02X).\n" % (
            field_name,
            ("%d" % start_bit) if num_bits == 1 else "%d:%d" % (start_bit, start_bit+num_bits-1),
            register_name,
            register_address)
            
    if rw == "R/W":
        docstring += "Readable and writable. "
    else:
        assert rw == "R"
        docstring += "Read only. "

    docstring += "Reset value %d.\n" % reset_value
    docstring += "Nordic Semiconductor documentation:\n"
    docstring += description

    return type(
            field_name,
            (_RegisterField,),
            dict(
                REGISTER_NAME=register_name,
                REGISTER_ADDRESS=register_address,
                FIELD_NAME=field_name,
                START_BIT=start_bit,
                NUM_BITS=num_bits,
                RESET_VALUE=reset_value,
                RW=rw,
                DESCRIPTION=description,
                __doc__=docstring
            )
        )


def _create_register_class(real_register_name, python_register_name, address,
                            min_size, max_size, fields, description):
    assert 1 <= min_size <= max_size
    assert all(issubclass(f, _RegisterField) for f in fields), repr(fields)
    assert 0 <= address <= 0x1F, "Address must be less than 0x1F, not %r" % address

    docstring = "Register %s at address 0x%02X. Size %s.\n" % (real_register_name, address,
                    ("%d byte" % min_size) if min_size==max_size else
                    ("%d-%d bytes" % (min_size, max_size)))
    if len(fields) > 0:
        docstring += "Fields: %s\n" % ", ".join(f.FIELD_NAME for f in fields)
    docstring += "Nordic Semiconductor documentation:\n"
    docstring += description

    return type(
            python_register_name,
            (_Register,),
            dict(
                NAME=python_register_name,
                ADDRESS=address,
                MIN_SIZE=min_size,
                MAX_SIZE=max_size,
                FIELDS=fields,
                DESCRIPTION=description,
                __doc__=docstring))


def _create_module_variables():
    """Adds all the register and field subclasses and assigns them to variables 
    in this Python module. e.g. PWR_UP, TX_FULL, REG_STATUS etc.
    """
    
    module = sys.modules[__name__]
    for register in _REGISTERS:
        fields = []
        for field in register.fields:
            assert not hasattr(module, field.name), "%s already defined" % field.name

            if field.name != "Reserved":
                class_ = _create_field_class(register.name,
                                            register.address,
                                            field.name,
                                            field.start_bit,
                                            field.num_bits,
                                            field.reset_value,
                                            field.rw,
                                            field.description)
                setattr(module, field.name, class_)
                fields.append(class_)

        python_register_name = "REG_" + register.name
        assert not hasattr(module, python_register_name)
        r = _create_register_class(register.name,
                                    python_register_name,
                                    register.address,
                                    register.min_size,
                                    register.max_size,
                                    tuple(fields),
                                    register.description)
        setattr(module, python_register_name, r)



_create_module_variables()


def _to_bytes(value):
    if isinstance(value, (int, long)):
        assert 0 <= value <= 255, "Value must be between 0 and 255 (inclusive), not %r" % value
        result = [value]
    elif isinstance(value, str):
        result = [ord(c) for c in value]
    else:
        assert isinstance(value, list), "Value must be an int, a string, or a list, not %r" % value
        assert all(isinstance(v, (int, long)) for v in value), "Value must only contain integers, not %r" % value
        assert all(0 <= v <= 255 for v in value), "Value must contain integers between 0 and 255, not %r" % value
        result = copy.copy(value)

    return result



def _tabulate(table):
    "Create a string showing a table of constant-width columns. Used for debugging."
    if not table:
        return ""

    column_sizes = [0] * len(table[0])
    for row in table:
        for i, cell in enumerate(row):
            column_sizes[i] = max(column_sizes[i], len(cell))

    lines = []
    for row in table:
        line = []
        for cell, size in zip(row, column_sizes):
            line.append(cell)
            line.append(" " * (size - len(cell)))
        lines.append("".join(line))

    return "\n".join(lines)





def _assert_valid_register_and_size(address, size):
    assert 0 <= address == int(address) <= 0x1D, "Invalid address %r" % address
    
    assert not 0x18 <= address <= 0x1B, (
            "Address %r reserved for test purposes by Nordic Semiconductor" % address)
    
    assert 1 <= size == int(size) <= 5, "size must be bewteen 1 and 5, not %r" % size
    
    is_multibyte_register = (0x0A <= address <= 0x0B or address == 0x10)
    assert (is_multibyte_register and 3 <= size <= 5 or
            not is_multibyte_register and size == 1)



class CancelFailedException(Exception):
    """Raised when trying to cancel a wait for an irq when no thread is waiting."""
    pass


class NRF24Gpio(object):
    """Used to control the chip enable (CE) pin on the nRF24 module and optionally wait for interrupts.
    This class is tailored to the GPIO module on Raspberry Pi, but you can provide your own object
    adhering to the interface of this class if you are running on another platform or have an
    unusual configuration requiring it (e.g. connecting the nRF24 CE pin via a shift register)."""
    
    def __init__(self, chip_enable_pin, irq_pin=None):
        """Using the rpi.GPIO module included in for example Raspbian.
        You need to configure the pins for input/output before using them.
        See NRF24Device constructor for example of usage.
        """
        self.chip_enable_pin = chip_enable_pin
        self.irq_pin = irq_pin

    def chip_enable_high(self):
        "Set the CE (chip enable) pin high."
        GPIO.output(self.chip_enable_pin, 1)

    def chip_enable_low(self):
        "Set the CE (chip enable) pin low."
        GPIO.output(self.chip_enable_pin, 0)

    def set_falling_edge_irq(self, callback):
        "Set a callback to be called (taking no arguments) if the IRQ pin goes low."
        assert self.irq_pin is not None
        GPIO.add_event_detect(self.irq_pin, GPIO.FALLING, callback=lambda channel: callback())
    
    def remove_falling_edge_irq(self):
        "Remove a callback previously set by set_falling_edge_irq()."
        assert self.irq_pin is not None
        GPIO.remove_event_detect(self.irq_pin)



class _WaitInfo(object):
    def __init__(self, condition_variable):
        
        # A lock supplied by the user to be released atomically when starting to wait for irq.
        self.condition_variable = condition_variable

        # True if we have already detected and dealt with the irq. Do nothing from the callback.
        self.already_finished = False

        # Set to True in the cancel_wait_for_irq() method
        self.cancelled = False
        


class NRF24Device(object):

    def __init__(self, spi, gpio):
        """Create an object representing a connected nRF24L01+ chip.
        spi is an object having the method xfer2([list_of_ints]) which sends the
        list of (8 bit) ints onto the SPI bus and returns a similar list of the bytes
        that were returned at the same time. You should probably use the SpiDev object
        from py-spidev, https://github.com/doceme/py-spidev .
        gpio is an object having at least two function, chip_enable_high() and 
        chip_enable_low(), which sets the CE (chip enable) pin on the nRF24L01+. If
        you use the wait_for_irq*() methods on NRF24Device the gpio object also needs
        the methods set_falling_edge_irq() and remove_falling_edge_irq() methods.
        Example:
            import spidev
            import RPi.GPIO as GPIO
            
            spi = spidev.SpiDev()
            spi.open(0, 0)
            spi.max_speed_hz = 10*1000*1000
        
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(17, GPIO.OUT)
            GPIO.setup(22, GPIO.IN)
        
            device = NRF24Device(spi=spi, gpio=NRF24Gpio(chip_enable_pin=17, irq_pin=22))
        """
        self._spi = spi
        self._gpio = gpio
        self._wait_info_cancellable = None
    
    def chip_enable_high(self):
        "Call chip_enable_high() on the gpio you supplied to the constructor."
        self._gpio.chip_enable_high()
    
    def chip_enable_low(self):
        "Call chip_enable_low() on the gpio you supplied to the constructor."
        self._gpio.chip_enable_low()
        
    def flush_tx_fifo(self):
        "Clear the TX FIFO and return the STATUS register."
        status = self._spi.xfer2([0b11100001])
        return status[0]

    def flush_rx_fifo(self):
        "Clear the RX FIFO and return the STATUS register."
        status = self._spi.xfer2([0b11100010])
        return status[0]

    def _write_payload(self, command, data):
        assert 1 <= len(data) <= 32, "Invalid length of payload %r" % (data,)
        status_and_junk = self._spi.xfer2([command] + _to_bytes(data))
        status = status_and_junk[0]
        return status
    
    def write_tx_payload(self, data):
        "Write TX payload, 1 to 32 bytes. Return STATUS register."
        return self._write_payload(0b10100000, data)
    
    def write_ack_payload(self, pipe, data):
        """Write 1 to 32 bytes of data to be transferred together with next ACK packet in the given pipe. 
        From nRF24L01+ product specification:
        "Used in RX mode.
        Write Payload to be transmitted together with ACK packet on PIPE PPP. (PPP valid in the range from 000 to 101). Maximum three ACK packet payloads can be pending. Payloads with same PPP are handled using first in - first out principle. Write payload: 1-32 bytes. A write operation always starts at byte 0." """
        assert 0 <= pipe <= 5, "Invalid pipe %r" % pipe
        return self._write_payload(0b10101000 | pipe, data)

    def write_tx_payload_no_ack(self, data):
        "Used in TX mode. Disables AUTOACK on this specific packet."
        return self._write_payload(0b10110000, data)
    
    def read_rx_payload(self, num_bytes):
        "Returns STATUS register and num_bytes of data"
        assert 1 <= num_bytes <= 32, "Invalid num_bytes: %r" % (num_bytes,)
        data = self._spi.xfer2([0b01100001] + [0] * num_bytes)
        status = data[0]
        return status, data[1:]
    
    def get_rx_payload_size(self):
        """Return STATUS and payload size of the first packet in RX FIFO.
        From nRF24L01+ product specification:
        "Read RX payload width for the top R_RX_PAYLOAD in the RX FIFO.
        Note: Flush RX FIFO if the read value is larger than 32 bytes." """
        data = spi.xfer2([0b01100000, _SPI_NOP])
        status, size = data[0], data[1]
        return status, size

    def reuse_tx_payload(self):
        """Resend the packet first in the TX FIFO. Return STATUS register.
        From nRF24L01+ product specification:
        "Used for a PTX device
        Reuse last transmitted payload.
        TX payload reuse is active until W_TX_PAYLOAD or FLUSH TX is executed. TX payload reuse must not be activated or deacti- vated during package transmission."
        """
        status_in_list = spi.xfer2([0b11100011])
        status = status_in_list[0]
        return status

    def get(self, *fields_or_registers):
        """Get register fields and 1 byte register. If you supply one parameter the function
        returns an int, if you supply 0 or >=2 parameters you will get a tuple with one int
        for each parameter.
        Examples:
        rx_full = device.get(RX_FULL)
        rx_full, arc_cnt, rx_addr_p5 = device.get(RX_FULL, ARC_CNT, REG_RX_ADDR_P5)
        """
    
        need_to_fetch_registers = set([REG_STATUS.ADDRESS])
        for r in fields_or_registers:
            if issubclass(r, _Register):
                assert r.MIN_SIZE == r.MAX_SIZE == 1, (
                        "Use get_register() to read variable size registers such as %s" % r.NAME)
                need_to_fetch_registers.add(r.ADDRESS)
            else:
                assert issubclass(r, _RegisterField)
                need_to_fetch_registers.add(r.REGISTER_ADDRESS)

        if need_to_fetch_registers != set([REG_STATUS.ADDRESS]):
            # Fetched implicitly when fetching another register
            need_to_fetch_registers.discard(REG_STATUS.ADDRESS)
        
        values = {}
        for address in need_to_fetch_registers:
            data = self._spi.xfer2([address, _SPI_NOP])
            status, register_value = tuple(data)
            values[REG_STATUS.ADDRESS] = status
            values[address] = register_value

        result = []
        for r in fields_or_registers:
            if issubclass(r, _Register):
                result.append(values[r.ADDRESS])
            else:
                result.append(r.get(values[r.REGISTER_ADDRESS]))
        
        if len(result) == 1:
            return result[0]
        else:
            return tuple(result)


    def get_register(self, address, size):
        """Returns a tuple with 2 elements. The first is an (8 bit) int with the value of the STATUS
        register. The second depends on size. If size == 1, the second element is an (8 bit) int. If
        size > 1, the second element is a list of (8 bit) ints with the specified size.
        Example: 
        get_variable_size_register(REG_RX_ADDR_P0, 4) 
        might return (0x0F, [0xC2, 0xC2, 0xC2, 0xC2])
        """
        _assert_valid_register_and_size(address, size)

        if address != REG_STATUS.ADDRESS:
            data = self._spi.xfer2([address] + [_SPI_NOP] * size)
            status = data[0]
            if size == 1:
                return status, data[1]
            else:
                return status, data[1:]
        else:
            status = self._spi.xfer2([_SPI_NOP])[0]
            return status, status
            
    
    
    def _set_register(self, address, value):
        """Set register at a specified address to value. Return STATUS register."""
        _assert_valid_register_and_size(address, len(value) if isinstance(value, list) else 1)
        
        data = self._spi.xfer2([0b00100000 | address] + _to_bytes(value))
        status = data[0]
        return status
    
    
    def _set_registers(self, *registers):
        """Write to registers. Return STATUS register if you provided at leats one argument.
        It's probably easier if you just use the set() function."""
        assert all(isinstance(r, _Register) for r in registers), "%r" % registers
        assert len(set(r.ADDRESS for r in registers)) == len(registers), (
                "One or more registers included multiple times")
    
        for r in registers:
            assert isinstance(r, _Register)
            status = self._set_register(r.ADDRESS, r.value)

        if len(registers) > 0:
            return status


    def _set_fields(self, *register_fields):
        """Set register fields and return STATUS if you provided at least one argument. 
        It's probably easier if you just use the set() function."""
        
        for rfs in register_fields:
            r = (rfs if isinstance(rfs, _RegisterFieldSet) else _RegisterFieldSet(rfs))

            register_address = r.get_register_address()
            mask = r.get_mask()
            value_to_write = r.get_value()

            if mask != 0xFF:
                status, old_value = self.get_register(register_address, size=1)
                value_to_write = value_to_write | (old_value & ~mask)
            
            status = self._set_register(register_address, value_to_write)
    
        if len(register_fields) > 0:
            return status


    def set(self, *registers_and_register_fields):
        """Set register(s) and/or field(s) to specified values. Return an (8 bit) int 
        with the contents of the STATUS register.
        Examples:
        device.set(REG_RX_ADDR_P0([0xE7, 0xE7, 0xE7, 0xE7, 0xE7]))
        status = device.set(PRIM_RX(1), PWR_UP(1))
        if TX_FULL.get(status):
            ...
        """
    
        assert len(registers_and_register_fields) >= 1
        assert all(isinstance(r, (_Register, _RegisterField, _RegisterFieldSet))
                for r in registers_and_register_fields)

        registers = {}
        for r in registers_and_register_fields:
            if isinstance(r, _Register):
                assert r.ADDRESS not in registers, "Register %s specified twice" % r.NAME
                registers[r.ADDRESS] = r

        register_field_sets = {}
        for r in registers_and_register_fields:
            if not isinstance(r, _Register):
                register_field = r if isinstance(r, _RegisterField) else r.fields[0]
                
                assert register_field.REGISTER_ADDRESS not in registers, (
                        "Register %s and RegisterField %s can't be written simultaneously" % (
                                register_field.REGISTER_NAME, register_field.FIELD_NAME))
                rfs = register_field_sets.setdefault(register_field.REGISTER_ADDRESS, _RegisterFieldSet())
                rfs |= r

        maybe_status_1 = self._set_registers(*registers.values())
        maybe_status_2 = self._set_fields(*register_field_sets.values())
        if maybe_status_2 is not None:
            return maybe_status_2
        else:
            return maybe_status_1


    def reset_to_default(self):
        """Set CE (Chip Enable) to low, flush all FIFOs and reset all registers to their reset values."""
        
        self.chip_enable_low()
        self.flush_tx_fifo()
        self.flush_rx_fifo()
    
        for r in _REGISTERS:
            rfs = _RegisterFieldSet()

            for f in r.fields:
                if "W" in f.rw and f.name not in ["Reserved", "Obsolete"]:
                    rfs |= getattr(sys.modules[__name__], f.name)(f.reset_value)

            if rfs.get_num_fields() != 0:
                self.set(rfs)

        # These have a reset value of 0 but are reset by writing 1 to them. So deal with them here.
        self.set(RX_DR(1) | TX_DS(1) | MAX_RT(1))

        self.set(REG_RX_ADDR_P0([0xE7, 0xE7, 0xE7, 0xE7, 0xE7]))
        self.set(REG_RX_ADDR_P1([0xC2, 0xC2, 0xC2, 0xC2, 0xC2]))
        self.set(REG_RX_ADDR_P2(0xC3))
        self.set(REG_RX_ADDR_P3(0xC4))
        self.set(REG_RX_ADDR_P4(0xC5))
        self.set(REG_RX_ADDR_P5(0xC6))
        self.set(REG_TX_ADDR([0xE7, 0xE7, 0xE7, 0xE7, 0xE7]))


    def register_to_string(self, register):
        """Returns a string representation of a register. Includes fields, values and descriptions.
        Useful for debugging.
        """
        assert issubclass(register, _Register), "%r" % (register,)

        values = self.get(*register.FIELDS)

        table = []
        for field, value in zip(register.FIELDS, values):
            table.append(
                (
                    "  %s (bit %s): " % (field.FIELD_NAME,
                                            ("%d" % field.START_BIT) if field.NUM_BITS == 1 else
                                                "%d:%d" % (field.START_BIT+field.NUM_BITS-1, field.START_BIT)),
                    "  %d  " % value,
                    field.DESCRIPTION.split("\n")[0][:80]
                ))

        table_str = _tabulate(table)

        return "Register %s at 0x%02x:\n" % (register.NAME, register.ADDRESS) + table_str


    def _internal_wait_for_irq_low(self, wait_info, timeout):
        start_time = time.time()

        def on_falling_edge_irq():
            # Called on another thread internal to gpio.
            with wait_info.condition_variable:
                if not wait_info.already_finished:
                    wait_info.condition_variable.notify_all()


        self._gpio.set_falling_edge_irq(on_falling_edge_irq)
        try:
            while True:
                mask_rx_dr, mask_tx_ds, mask_max_rt, rx_dr, tx_ds, max_rt = self.get(
                        MASK_RX_DR, MASK_TX_DS, MASK_MAX_RT, RX_DR, TX_DS, MAX_RT)
                
                elapsed_time = time.time() - start_time
                if (rx_dr and not mask_rx_dr or
                        tx_ds and not mask_tx_ds or
                        max_rt and not mask_max_rt or
                        timeout is not None and elapsed_time >= timeout or
                        wait_info.cancelled):
                    break
                    
                wait_info.condition_variable.wait(None if timeout is None else (timeout - elapsed_time))
        finally:
            wait_info.already_finished = True
            self._gpio.remove_falling_edge_irq()
        
        

    def wait_for_irq_low(self, timeout=None):
        """Wait until the IRQ gets low or until timeout seconds has passed.
        You can _not_ cancel this wait by calling cancel_wait_for_irq() on another thread,
        you must use the method wait_for_irq_low_cancellable() for that.
        Note that this function does use gpio internally so if you need to protect that with
        some kind of mutex you should use wait_for_irq_low_cancellable() which takes a
        condition_variable as parameter and releases it before going to sleep.
        """
        assert self._wait_info_cancellable is None
        wait_info = _WaitInfo(threading.Condition())
        with wait_info.condition_variable:
            self._internal_wait_for_irq_low(wait_info, timeout)
        
        
    def wait_for_irq_low_cancellable(self, condition_variable, timeout=None):
        """Wait until the IRQ gets low or until timeout seconds has passed. You can cancel 
        the wait from another thread using cancel_wait_for_irq() if you supply the same 
        condition_variable in both calls. You must have acquired the condition_variable before 
        calling these functions. Also note that the condition_variable may be used by another
        thread internal to this object. You should probably use the same condition_variable to 
        protect all access to GPIO throughout your program.
        """
        assert self._wait_info_cancellable is None
        self._wait_info_cancellable = _WaitInfo(condition_variable)
        self._internal_wait_for_irq_low(self._wait_info_cancellable, timeout)
        self._wait_info_cancellable = None
        
        
    def cancel_wait_for_irq(self, condition_variable):
        """Call this to wake up the thread inside wait_for_irq_low_cancellable(). An
        exception is thrown if there is no thread currently in that function.
        You must have acquired the condition_variable before calling this function and
        it must be the same condition_variable passed to the wait function."""
        if self._wait_info_cancellable is None:
            raise CancelFailedException("No thread waiting for irq " +
                    "(or it's waiting using the function wait_for_irq_low() which is not "
                    "cancellable)")
        assert condition_variable == self._wait_info_cancellable.condition_variable
        self._wait_info_cancellable.cancelled = True
        condition_variable.notify_all()
        

