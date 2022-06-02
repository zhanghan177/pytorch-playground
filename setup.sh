#!/usr/bin/env bash

# Setup script for freshly installed Ubuntu box (with Pytorch pre-installed)
pip install opencv-python

sudo apt update -qq -y
sudo apt-get install python3-opencv -y