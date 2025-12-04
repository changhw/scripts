#!/bin/bash

cd ~

#resol=1278x798  # Laptop Vollbild
#resol=1278x742  # Laptop
#resol=1278x980 # Display mit 1280x1024
#resol=1398x1020 # Display mit 1400x1050
#resol=1598x1010 # Display mit 1600x1200 oder 1680x1050 (Vollbild)
#resol=1480x942 # x1028
resol=2500x1350

vncserver -fp "built-ins" -depth 24 -geometry $resol :79

fbsetroot -solid rgb:00/66/88 &
