import collections

from config import *
from nrf24 import *

import RPi.GPIO as GPIO
import spidev


def int_to_32_bytes(x):
    assert 0 <= x < 2**32
    data = [x & 0xFF,
            (x >> 8) & 0xFF,
            (x >> 16) & 0xFF,
            (x >> 24) & 0xFF] * 8
    return data


def packet_to_int(packet):
    for i in range(4, 32, 4):
        if packet[i:i+4] != packet[0:4]:
            return -1
    x = (packet[0] |
            (packet[1] << 8) |
            (packet[2] << 16) |
            (packet[3] << 24))
    return x


def test_throughput():

    DEVICE_RX = 0
    DEVICE_TX = 1
    
    GPIO.setmode(GPIO.BCM)
    try:
        GPIO.setwarnings(True)

        GPIO.setup(CHIP0_CE, GPIO.OUT)
        GPIO.setup(CHIP1_CE, GPIO.OUT)

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

        TRANSMISSION_SPEED_MBPS = 2 # 0.250 or 1 or 2
        PACKET_TRANSMISSION_TIME = (1+3+32)*8/(TRANSMISSION_SPEED_MBPS*1000*1000)


        for device in (device_rx, device_tx):
            device.flush_tx_fifo()
            device.flush_rx_fifo()
        
            # Disable CRC
            device.set(EN_CRC(0) | CRCO(0))
            
            # Disable auto-ACK
            device.set(ENAA_P0(0) | ENAA_P1(0) | ENAA_P2(0) | ENAA_P3(0) | ENAA_P4(0) | ENAA_P5(0))
            
            # Enable pipe 0, disable the others
            device.set(ERX_P0(1) | ERX_P1(0) | ERX_P2(0) | ERX_P3(0) | ERX_P4(0) | ERX_P5(0))
            
            # Set address width to 3 bytes
            device.set(AW(1))
            
            # Disable retransmit
            device.set(ARC(0))
            
            # Set frequency to 2400 + 25 = 2025 MHz
            device.set(RF_CH(25))
            
            # Set link speed.
            rf_dr = {0.25: RF_DR_LOW(1) | RF_DR_HIGH(0),
                     1: RF_DR_LOW(0) | RF_DR_HIGH(0),
                     2: RF_DR_LOW(0) | RF_DR_HIGH(1)
                }[TRANSMISSION_SPEED_MBPS]
            
            device.set(rf_dr)
            
            # Set received payload size in pipe 0 to 32 bytes.
            device.set(RX_PW_P0(32))
            
            # Set address for receive and transmit
            address = [0b11100111, 0b00110001, 0b11010111]
            device.set(REG_RX_ADDR_P0(address))
            device.set(REG_TX_ADDR(address))

        device_rx.chip_enable_low()
        device_tx.chip_enable_low()

        # Set up one device to transmit, one to receive, and power on
        device_rx.set(PWR_UP(1) | PRIM_RX(1))
        device_tx.set(PWR_UP(1) | PRIM_RX(0))
        
        # Must wait for a while for the oscillator to startup (depends on the crystal you use)
        time.sleep(0.01)

        # Both devices are now in mode Standby-I. Take RX device to RX mode
        device_rx.chip_enable_high()
        
        # Required waiting time 130 microseconds before we are in RX mode
        time.sleep(0.000130)
        
        # According to the datasheet, we can't be in TX mode for longer than about 4 ms, otherwise
        # the frequency can drift off making it impossible to receive whatever is sent or something
        # like that.
        in_tx_mode = False
        last_enter_tx = 0
        last_exit_tx = 0
        
        num_rx_full_encountered = 0
        
        start_time = time.time()
        num_packets_to_send = 500
        num_packets_sent = 0
        
        packets_received = [0] * num_packets_to_send
        corrupt_packets = []
        
        last_packet_received_time = 0
        
        while True:
            
            # Can't touch tx device spi bus until 4 microseconds has passed since we set CE = 1
            if time.time() - last_enter_tx > 0.000004:

                # Can't be in TX mode longer than 4 ms and it takes some time to transfer a packet.
                if in_tx_mode and time.time() - last_enter_tx > 0.004 - PACKET_TRANSMISSION_TIME:
                    device_tx.set(TX_DS(1))  # Clear Data Sent bit by writing 1 to it
                    in_tx_mode = False
                    device_tx.chip_enable_low()
                    last_exit_tx = time.time()

                # Read from FIFO_STATUS register. Note the underscore in TX_FULL_ . It's needed because
                # of a naming conflict - TX_FULL is also present in the STATUS register.
                tx_full, tx_empty = device_tx.get(TX_FULL_, TX_EMPTY)
                need_to_enter_tx_mode = not tx_empty
                if not tx_full and num_packets_sent < num_packets_to_send:
                    device.write_tx_payload(int_to_32_bytes(num_packets_sent))
                    num_packets_sent += 1
                    need_to_enter_tx_mode = True
                
                # Don't enter TX mode unless we have sent something or been out of TX mode for a long time
                if (need_to_enter_tx_mode and
                        not in_tx_mode and (
                            device_tx.get(TX_DS) == 1 or
                            time.time() - last_exit_tx > PACKET_TRANSMISSION_TIME)):
                    in_tx_mode = True
                    device_tx.chip_enable_high()
                    last_enter_tx = time.time()
        
                
            rx_full, rx_empty = device_rx.get(RX_FULL, RX_EMPTY)
            if rx_full:
                # We might have dropped packets if this happens...
                num_rx_full_encountered += 1
            if not rx_empty:
                status, packet = device_rx.read_rx_payload(32)
                
                x = packet_to_int(packet)
                if 0 <= x < len(packets_received):
                    packets_received[x] += 1
                    last_packet_received_time = time.time()
                else:
                    corrupt_packets.append(packet)
            
            # Stop when we have sent all and waited max 50 ms since we last tried to send, or if
            # it passed 2 seconds since last try to send in which case something is wrong.
            if (num_packets_sent == num_packets_to_send and
                    rx_empty and
                    time.time() - last_enter_tx > 0.000004 and
                    device_tx.get(TX_EMPTY)):
                break

        device_rx.chip_enable_low()
        device_tx.chip_enable_low()
        
        device_rx.set(PWR_UP(0))
        device_tx.set(PWR_UP(0))
        
        num_packets_received = len(packets_received) - packets_received.count(0)
        print("Received %d of %d packets (%.2f %%) in %.2f seconds" % (
                num_packets_received,
                num_packets_sent,
                100.0 * num_packets_received / num_packets_sent,
                (last_packet_received_time or time.time()) - start_time))
        
        if num_packets_received > 0:
            transmission_time = last_packet_received_time - start_time
            print("%.0f packets per second, %.0f bytes payload per second, %.0f bits per second line speed" % (
                    num_packets_received / transmission_time,
                    32*num_packets_received / transmission_time,
                    (1+3+32)*8*num_packets_received / transmission_time))
        
        if num_rx_full_encountered > 0:
            print("Encountered a full RX FIFO %d times in the loop, " % num_rx_full_encountered +
                    "meaning packets could have been dropped.")
        
        if 0 < num_packets_received < num_packets_sent:
            print("Missing packets (indices): %s" % ", ".join(
                    [str(i) for i, p in enumerate(packets_received) if p == 0][:10] +
                        ([] if packets_received.count(0) <= 10 else ["..."])))
        
        if corrupt_packets:
            print("Received %d corrupt packets:" % len(corrupt_packets))
            for p in corrupt_packets[:10]:
                print("  %r" % p)
            if len(corrupt_packets) > 10:
                print("  ...")
        elif num_packets_received > 0:
            print("All packets ok")
        else:
            print("No packets at all received (neither ok nor corrupt).")
        
        if max(packets_received) in [0, 1]:
            print("No duplicate packets")
        else:
            print("Number of duplicates / Number of occurences")
            for num_duplicates, num_times in collections.Counter(packets_received).items():
                if num_duplicates >= 2:
                    print("  %d / %d" % (num_duplicates, num_times))

    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    test_throughput()
