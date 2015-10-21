

# CE output pins
CHIP0_CE = 17
CHIP1_CE = 25

CE = (CHIP0_CE, CHIP1_CE)

# IRQ pins
CHIP0_IRQ = 22
CHIP1_IRQ = 24

IRQ = (CHIP0_IRQ, CHIP1_IRQ)


if __name__ == "__main__":
    print("This is not intended to be run, but to describe how the nRF24L01+ chips "
            "are connected to the Raspberry Pi")
