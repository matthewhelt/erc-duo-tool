# ERC DUO Tool

## Summary

This tool was devised to assist with the configuration and calibration of the Wimo ERC-DUO interface as used with a Yaesu G5500 rotator system. You probably want to use the real and supported Wimo calibration software, this was a learning experiment to help understand how this system works.

## Use

* Create a virtual environment
* Use pip to install requirements - it's probably more than you need
* Note the serial tty device path of the interface you have, I prefer `/dev/serial/by-id/` enumerators if available on your system.
* Run `python -m ercduo` - a TUI will display.
* Select your device, verify coordinate reads, proceed to calibrate or control as needed.  
