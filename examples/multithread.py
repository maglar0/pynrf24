import threading

from config import *
from nrf24 import *

import RPi.GPIO as GPIO
import spidev



def test_multithread():
    DEVICE_RX = 1
    DEVICE_TX = 0

    condition_variable = threading.Condition()

    GPIO.setmode(GPIO.BCM)
    try:
        GPIO.setwarnings(True)

        GPIO.setup(CHIP0_CE, GPIO.OUT)
        GPIO.setup(CHIP1_CE, GPIO.OUT)

        GPIO.setup(CHIP0_IRQ, GPIO.IN)
        GPIO.setup(CHIP1_IRQ, GPIO.IN)

        spi_rx = spidev.SpiDev()
        spi_rx.open(0, DEVICE_RX)
        spi_rx.max_speed_hz = 10*1000*1000
        device_rx = NRF24Device(spi_rx, NRF24Gpio(CE[DEVICE_RX], IRQ[DEVICE_RX]))
        device_rx.chip_enable_low()
        device_rx.reset_to_default()
        device_rx.set(PRIM_RX(1), PWR_UP(1))
        device_rx.set(RX_PW_P0(1))

        spi_tx = spidev.SpiDev()
        spi_tx.open(0, DEVICE_TX)
        spi_tx.max_speed_hz = 10*1000*1000
        device_tx = NRF24Device(spi_tx, NRF24Gpio(CE[DEVICE_TX], IRQ[DEVICE_TX]))
        device_tx.chip_enable_low()
        device_tx.reset_to_default()
        device_tx.set(PWR_UP(1))
        
        stop = False
        rx_waiting = [False]
        
        def tx_thread():
            for i in range(42, 52):
                time.sleep(0.1)
                with condition_variable:
                    # Set payload and go to TX mode
                    device_tx.write_tx_payload([i])
                    device_tx.chip_enable_high()
        
                # This is probably too small to even care about in a Python program,
                # but the datasheet says minimum wait of 4 microseconds (Tpece2csn)
                # after going CE high to going CSN low (i.e. sending SPI commands).
                time.sleep(4e-6)
        
                with condition_variable:
                    # Should use wait_for_irq_low_cancellable() even though we will
                    # never cancel it since it uses GPIO internally and it should thus
                    # release the condition_variable before going to sleep.
                    device_tx.wait_for_irq_low_cancellable(condition_variable, 0.1)
                    
                    tx_ds, max_rt = device_tx.get(TX_DS, MAX_RT)
                    if tx_ds:
                        print("Data %d sent successfully" % i)
                        device_tx.set(TX_DS(1)) # Clear irq by writing 1
                    elif max_rt:
                        print("Data %d sent but no ACK received" % i)
                        device_tx.set(MAX_RT(1)) # Clear irq by writing 1
                    else:
                        print("TX: Unexpected state after sending data %d" % i)
                        print(device_tx.register_to_string(REG_CONFIG))
                        print(device_tx.register_to_string(REG_STATUS))
                        print(device_tx.register_to_string(REG_FIFO_STATUS))
                        print(device_tx.register_to_string(REG_OBSERVE_TX))

                    device_tx.chip_enable_low()

        def rx_thread():
            with condition_variable:
                # Need to wait Tpd2stby until we are in Standby-I mode
                time.sleep(0.01)
                
                device_rx.chip_enable_high()

                # This is probably too small to even care about in a Python program,
                # but the datasheet says minimum wait of 4 microseconds (Tpece2csn)
                # after going CE high to going CSN low (i.e. sending SPI commands).
                time.sleep(4e-6)

            with condition_variable:
                for i in range(15):
                    if stop:
                        print("Stopping")
                        break
                    else:
                        print("")
                    rx_waiting[0] = True
                    device_rx.wait_for_irq_low_cancellable(condition_variable)
                    rx_waiting[0] = False
                    rx_dr = device_rx.get(RX_DR)
                    if rx_dr:
                        status, payload = device_rx.read_rx_payload(1)
                        device_rx.set(RX_DR(1)) # Clear irq by writing 1
                        print("Got rx payload %r" % payload[0])
                    elif stop:
                        print("RX wait cancelled by main thread")
                    else:
                        print("RX: Unexpected state")
                        print(device_rx.register_to_string(REG_CONFIG))
                        print(device_rx.register_to_string(REG_STATUS))
                        print(device_rx.register_to_string(REG_FIFO_STATUS))
                        print(device_rx.register_to_string(REG_OBSERVE_TX))
    
        rx_thread = threading.Thread(target=rx_thread)
        rx_thread.start()

        tx_thread = threading.Thread(target=tx_thread)
        tx_thread.start()

        tx_thread.join(2)
        if tx_thread.is_alive():
            print("Something went wrong, would have expected the TX thread to be finished by now.")
        tx_thread.join()

        with condition_variable:
            stop = True
            if rx_waiting[0]:
                device_rx.cancel_wait_for_irq(condition_variable)
        rx_thread.join(2)
        if rx_thread.is_alive():
            print("Something went wrong, would have expected the RX thread to be finished by now.")
        rx_thread.join()

    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    test_multithread()
