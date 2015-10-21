from config import *
from nrf24 import *

import RPi.GPIO as GPIO
import spidev





def send_and_receive_one():
    
    DEVICE_RX = 0
    DEVICE_TX = 1
    
    GPIO.setmode(GPIO.BCM)
    try:
        GPIO.setwarnings(True)

        GPIO.setup(CHIP0_IRQ, GPIO.IN)
        GPIO.setup(CHIP1_IRQ, GPIO.IN)

        GPIO.setup(CHIP0_CE, GPIO.OUT)
        GPIO.setup(CHIP1_CE, GPIO.OUT)

        print("Chip RX IRQ: %r" % GPIO.input(IRQ[DEVICE_RX]))
        print("Chip TX IRQ: %r" % GPIO.input(IRQ[DEVICE_TX]))

        spi_rx = spidev.SpiDev()
        spi_rx.open(0, DEVICE_RX)
        spi_rx.max_speed_hz = 10*1000*1000
        device_rx = NRF24Device(spi_rx, NRF24Gpio(CE[DEVICE_RX], IRQ[DEVICE_RX]))
        device_rx.reset_to_default()

        spi_tx = spidev.SpiDev()
        spi_tx.open(0, DEVICE_TX)
        spi_tx.max_speed_hz = 10*1000*1000
        device_tx = NRF24Device(spi_tx, NRF24Gpio(CE[DEVICE_TX], IRQ[DEVICE_TX]))
        device_tx.reset_to_default()

        print("Config for rx device (device %d):" % DEVICE_RX)
        print(device_rx.register_to_string(REG_CONFIG))
        print("")

        print("Config for tx device (device %d):" % DEVICE_TX)
        print(device_tx.register_to_string(REG_CONFIG))
        print("")
        
        for device in (device_rx, device_tx):
            device.flush_tx_fifo()
            device.flush_rx_fifo()
        
            device.set(EN_CRC(0))
            device.set(ENAA_P0(0) | ENAA_P1(0) | ENAA_P2(0) | ENAA_P3(0) | ENAA_P4(0) | ENAA_P5(0))
            device.set(AW(1))
            device.set(RF_CH(0b100))
            device.set(RF_DR_LOW(0) | RF_DR_HIGH(0))
            device.set(RX_PW_P0(32))
            device.set(REG_RX_ADDR_P0([0b10100110, 0b10110001, 0b01011101]))
            device.set(REG_TX_ADDR([0b10100110, 0b10110001, 0b01011101]))

            print(device.register_to_string(REG_STATUS))

        device_rx.set(RX_DR(1))
        device_tx.set(TX_DS(1))

        print("Chip RX IRQ: %r" % GPIO.input(IRQ[DEVICE_RX]))
        print("Chip TX IRQ: %r" % GPIO.input(IRQ[DEVICE_TX]))

        time.sleep(1)

        device_rx.set(PWR_UP(1) | PRIM_RX(1))
        device_tx.set(PWR_UP(1) | PRIM_RX(0))
        time.sleep(0.01)
        
        # We are now in mode Standby-I

        device_tx.write_tx_payload(range(0, 64, 2))
        time.sleep(0.0001)

        GPIO.output(CE[DEVICE_RX], 1)
        GPIO.output(CE[DEVICE_TX], 1)
        time.sleep(0.001)

        # We are now in TX/RX mode

        print("We are now in RX/TX mode")
        print("Chip RX IRQ: %r" % GPIO.input(IRQ[DEVICE_RX]))
        print("Chip TX IRQ: %r" % GPIO.input(IRQ[DEVICE_TX]))
        print("Detecting carrier wave: %d" % device_rx.get(RPD))
        print("")

        print("Status register for RX device:")
        print(device_rx.register_to_string(REG_STATUS))

        print("Status register for TX device:")
        print(device_tx.register_to_string(REG_STATUS))

        GPIO.output(CE[DEVICE_RX], 0)
        GPIO.output(CE[DEVICE_TX], 0)
        time.sleep(0.001)
        
        time.sleep(5)

        print("Chip RX IRQ: %r" % GPIO.input(IRQ[DEVICE_RX]))
        print("Chip TX IRQ: %r" % GPIO.input(IRQ[DEVICE_TX]))

        for device in (device_rx, device_tx):
            print("RX_FULL: %d, RX_EMPTY: %d, TX_FULL: %d, TX_EMPTY: %d" %
                device.get(RX_FULL, RX_EMPTY, TX_FULL_, TX_EMPTY))
            for i in range(4):
                rx_empty = device.get(RX_EMPTY)
                if not rx_empty:
                    status, data = device.read_rx_payload(32)
                    print("Received payload %d: %r" % (i, data))
                    print("RX_FULL: %d, RX_EMPTY: %d, TX_FULL: %d, TX_EMPTY: %d" %
                        device.get(RX_FULL, RX_EMPTY, TX_FULL_, TX_EMPTY))
            print("")
    

        # We are now in mode Standby-I

        device_rx.set(PWR_UP(0))
        device_tx.set(PWR_UP(0))

    finally:
        GPIO.cleanup()



if __name__ == "__main__":
    send_and_receive_one()
