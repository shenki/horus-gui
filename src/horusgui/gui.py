#!/usr/bin/env python
#
#   Horus Telemetry GUI
#
#   Mark Jessop <vk5qi@rfhead.net>
#


# Python 3 check
import sys

if sys.version_info < (3, 0):
    print("This script requires Python 3!")
    sys.exit(1)

import glob
import logging
import pyqtgraph as pg
import numpy as np
from queue import Queue
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
from pyqtgraph.dockarea import *
from threading import Thread

from .widgets import *
from .audio import *
from .fft import *
from .modem import *


# Setup Logging
logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO
)

# Defaults

DEFAULT_CALLSIGN = 'N0CALL'

# Global widget store
widgets = {}

# Queues for handling updates to image / status indications.
fft_update_queue = Queue(256)
status_update_queue = Queue(256)

# List of audio devices and their info
audio_devices = {}

# Processor objects
audio_stream = None
fft_process = None
horus_modem = None

# Global running indicator
running = False

#
#   GUI Creation - The Bad way.
#

# Create a Qt App.
pg.mkQApp()

# GUI LAYOUT - Gtk Style!
win = QtGui.QMainWindow()
area = DockArea()
win.setCentralWidget(area)
win.setWindowTitle("Horus Telemetry GUI")

# Create multiple dock areas, for displaying our data.
d0 = Dock("Controls", size=(300,800))
d1 = Dock("Spectrum", size=(800,500))
d2 = Dock("Waterfall", size=(800,200))
d3 = Dock("Telemetry",size=(800,200))
area.addDock(d0, "left")
area.addDock(d1, "right", d0)
area.addDock(d2, "bottom", d1)
area.addDock(d3, "bottom", d2)



# Controls
w1 = pg.LayoutWidget()
# TNC Connection
widgets['audioDeviceLabel'] = QtGui.QLabel("<b>Audio Device:</b>")
widgets['audioDeviceSelector'] = QtGui.QComboBox()

widgets['audioSampleRateLabel'] = QtGui.QLabel("<b>Sample Rate (Hz):</b>")
widgets['audioSampleRateSelector'] = QtGui.QComboBox()

# Modem Parameters
widgets['horusModemLabel'] = QtGui.QLabel("<b>Mode:</b>")
widgets['horusModemSelector'] = QtGui.QComboBox()

widgets['horusModemRateLabel'] = QtGui.QLabel("<b>Baudrate:</b>")
widgets['horusModemRateSelector'] = QtGui.QComboBox()

widgets['horusMaskEstimatorLabel'] = QtGui.QLabel("<b>Enable Mask Estim.:</b>")
widgets['horusMaskEstimatorSelector'] = QtGui.QCheckBox()

widgets['horusMaskSpacingLabel'] = QtGui.QLabel("<b>Tone Spacing (Hz):</b>")
widgets['horusMaskSpacingEntry'] = QtGui.QLineEdit("270")

# Start/Stop
widgets['startDecodeButton'] = QtGui.QPushButton("Start")

# Listener Information
widgets['userCallLabel'] = QtGui.QLabel("<b>Callsign:</b>")
widgets['userCallEntry'] = QtGui.QLineEdit(DEFAULT_CALLSIGN)
widgets['userCallEntry'].setMaxLength(20)



# Layout the Control pane.
# Yes this is horrible. Don't @ me.
w1.addWidget(widgets['audioDeviceLabel'], 0, 0, 1, 1)
w1.addWidget(widgets['audioDeviceSelector'], 0, 1, 1, 1)
w1.addWidget(widgets['audioSampleRateLabel'], 1, 0, 1, 1)
w1.addWidget(widgets['audioSampleRateSelector'], 1, 1, 1, 1)
w1.addWidget(QHLine(), 2, 0, 1, 2)
w1.addWidget(widgets['horusModemLabel'], 3, 0, 1, 1)
w1.addWidget(widgets['horusModemSelector'], 3, 1, 1, 1)
w1.addWidget(widgets['horusModemRateLabel'], 4, 0, 1, 1)
w1.addWidget(widgets['horusModemRateSelector'], 4, 1, 1, 1)
w1.addWidget(widgets['horusMaskEstimatorLabel'], 5, 0, 1, 1)
w1.addWidget(widgets['horusMaskEstimatorSelector'], 5, 1, 1, 1)
w1.addWidget(widgets['horusMaskSpacingLabel'], 6, 0, 1, 1)
w1.addWidget(widgets['horusMaskSpacingEntry'], 6, 1, 1, 1)
w1.addWidget(QHLine(), 7, 0, 1, 2)
w1.addWidget(widgets['startDecodeButton'], 8, 0, 1, 2)
w1.addWidget(QHLine(), 9, 0, 1, 2)
w1.addWidget(widgets['userCallLabel'], 10, 0, 1, 1)
w1.addWidget(widgets['userCallEntry'], 10, 1, 1, 1)

w1.layout.setSpacing(1)
d0.addWidget(w1)


# Spectrum Display
widgets['spectrumPlot'] = pg.PlotWidget(title="Spectra")
widgets['spectrumPlot'].setLabel("left", "Power (dB)")
widgets['spectrumPlot'].setLabel("bottom", "Frequency (Hz)")
widgets['spectrumPlotData']= widgets['spectrumPlot'].plot([0])

widgets['spectrumPlot'].setLabel('left', "Power (dBFs)")
widgets['spectrumPlot'].setLabel('bottom', "Frequency", units="Hz")
widgets['spectrumPlot'].setXRange(100,4000)
widgets['spectrumPlot'].setYRange(-110,-20)
widgets['spectrumPlot'].setLimits(xMin=0, xMax=4000, yMin=-120, yMax=0)
d1.addWidget(widgets['spectrumPlot'])

widgets['spectrumPlotRange'] = [-110, -20]

# Waterfall - TBD
w3 = pg.LayoutWidget()
d2.addWidget(w3)

# Telemetry Data
w4 = pg.LayoutWidget()
rxImageStatus = QtGui.QLabel("No Data Yet.")
w4.addWidget(rxImageStatus, 0, 0, 1, 1)
d3.addWidget(w4)

# Resize window to final resolution, and display.
logging.info("Starting GUI.")
win.resize(1500, 800)
win.show()

# Audio Initialization 
audio_devices = init_audio(widgets)

def update_audio_sample_rates():
    """ Update the sample-rate dropdown when a different audio device is selected.  """
    global widgets
    # Pass widgets straight on to function from .audio
    populate_sample_rates(widgets)

widgets['audioDeviceSelector'].currentIndexChanged.connect(update_audio_sample_rates)

# Initialize modem list.
init_horus_modem(widgets)

def update_modem_settings():
    """ Update the modem setting widgets when a different modem is selected """
    global widgets
    populate_modem_settings(widgets)

widgets['horusModemSelector'].currentIndexChanged.connect(update_modem_settings)


def handle_fft_update(data):
    """ Handle a new FFT update """
    global widgets

    _scale = data['scale']
    _data = data['fft']

    widgets['spectrumPlotData'].setData(_scale, _data)

    # Really basic IIR to smoothly adjust scale
    _old_max = widgets['spectrumPlotRange'][1]
    _tc = 0.1
    _new_max = float((_old_max*(1-_tc)) + (np.max(_data)*_tc))

    # Store new max
    widgets['spectrumPlotRange'][1] = _new_max

    widgets['spectrumPlot'].setYRange(-110, min(0,_new_max)+20)



def add_fft_update(data):
    """ Try and insert a new set of FFT data into the update queue """
    global fft_update_queue
    try:
        fft_update_queue.put_nowait(data)
    except:
        logging.error("FFT Update Queue Full!")


def start_decoding():
    global widgets, audio_stream, fft_process, horus_modem, audio_devices, running

    if not running:
        # Grab settings off widgets
        _dev_name = widgets['audioDeviceSelector'].currentText()
        _sample_rate = int(widgets['audioSampleRateSelector'].currentText())
        _dev_index = audio_devices[_dev_name]['index']

        # TODO: Grab horus data here.


        # Init FFT Processor
        fft_process = FFTProcess(
            nfft=8192,
            stride=4096,
            fs=_sample_rate,
            callback=add_fft_update
        )

        # TODO: Setup modem here

        # Setup Audio
        audio_stream = AudioStream(
            _dev_index,
            fs = _sample_rate,
            block_size=fft_process.stride,
            fft_input = fft_process.add_samples,
            modem=None
        )

        widgets['startDecodeButton'].setText('Stop')
        running = True

    else:

        try:
            audio_stream.stop()
        except Exception as e:
            logging.exception("Could not stop audio stream.", exc_info=e)
        
        try:
            fft_process.stop()
        except Exception as e:
            logging.exception("Could not stop fft processing.", exc_info=e)

        widgets['startDecodeButton'].setText('Start')
        running = False


widgets['startDecodeButton'].clicked.connect(start_decoding)



# GUI Update Loop
def processQueues():
    """ Read in data from the queues, this decouples the GUI and async inputs somewhat. """
    global fft_update_queue, status_update_queue

    while fft_update_queue.qsize() > 0:
        _data = fft_update_queue.get()

        handle_fft_update(_data)

    while status_update_queue.qsize() > 0:
        _status = status_update_queue.get()
        # Handle Status updates here.


gui_update_timer = QtCore.QTimer()
gui_update_timer.timeout.connect(processQueues)
gui_update_timer.start(100)


# Main
def main():
    # Start the Qt Loop
    if (sys.flags.interactive != 1) or not hasattr(QtCore, "PYQT_VERSION"):
        QtGui.QApplication.instance().exec_()
    
    try:
        audio_stream.stop()
    except Exception as e:
        logging.exception("Could not stop audio stream.", exc_info=e)
    
    try:
        fft_process.stop()
    except Exception as e:
        logging.exception("Could not stop fft processing.", exc_info=e)


if __name__ == "__main__":
    main()

