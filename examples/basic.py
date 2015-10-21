from config import *
from nrf24 import *

import RPi.GPIO as GPIO
import spidev


def test_basic():
    DEVICE_RX = 1
    DEVICE_TX = 0

    GPIO.setmode(GPIO.BCM)
    try:
        GPIO.setwarnings(True)

        GPIO.setup(CHIP0_CE, GPIO.OUT)
        GPIO.setup(CHIP1_CE, GPIO.OUT)

        spi_rx = spidev.SpiDev()
        spi_rx.open(0, DEVICE_RX)
        spi_rx.max_speed_hz = 10*1000*1000
        device_rx = NRF24Device(spi_rx, NRF24Gpio(CE[DEVICE_RX]))
        device_rx.reset_to_default()
        device_rx.set(PRIM_RX(1), PWR_UP(1))
        device_rx.set(RX_PW_P0(32))

        spi_tx = spidev.SpiDev()
        spi_tx.open(0, DEVICE_TX)
        spi_tx.max_speed_hz = 10*1000*1000
        device_tx = NRF24Device(spi_tx, NRF24Gpio(CE[DEVICE_TX]))
        device_tx.reset_to_default()
        device_tx.set(PWR_UP(1))
        
        try:
            # Need to wait Tpd2stby until we are in Standby-I mode
            time.sleep(0.01)
            
            device_rx.chip_enable_high()
            
            # After 130 microseconds the RX device is in RX mode
            time.sleep(0.000130)
            
            # Set payload and go to TX mode
            data_to_send = range(32)
            device_tx.write_tx_payload(data_to_send)
            device_tx.chip_enable_high()
            
            start_time = time.time()

            transmitted = False
            while not transmitted and time.time() < start_time+1:
                tx_ds, max_rt = device_tx.get(TX_DS, MAX_RT)
                if tx_ds:
                    print("Data sent and ACK received after %d retransmissions" % device_tx.get(ARC_CNT))
                    transmitted = True
                elif max_rt:
                    print("Transmitted %d times without ACK in %.1f milliseconds, giving up" %
                            (device_tx.get(ARC_CNT), (time.time() - start_time)*1000))
                    transmitted = True

            if not transmitted:
                print("Could not get ACK for transmitted packet")
            
            received = False
            while not received and time.time() < start_time+1:
                rx_dr = device_rx.get(RX_DR)
                if rx_dr:
                    assert not device_rx.get(RX_EMPTY)
                    status, payload = device_rx.read_rx_payload(len(data_to_send))
                    if payload == data_to_send:
                        print("Data received")
                    else:
                        print("Invalid data received:")
                        print("  Expected %r" % data_to_send)
                        print("  Got      %r" % payload)
                    received = True

            if not received:
                print("Data not received")

        finally:
            device_rx.chip_enable_low()
            device_tx.chip_enable_low()
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    test_basic()
