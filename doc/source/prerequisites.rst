======================================
Prerequisites for running FLAC Manager
======================================

.. note::

   FLAC Manager requires `Python 3.3+ <http://www.python.org/>`_ and the
   :py:mod:`tkinter` module to run.

FLAC Manager has several software prerequisites. Each of the following can be
installed via `MacPorts <http://www.macports.org/>`_, or follow the links to
the original project pages to download sources and build/install yourself.

* `flac - Command-line FLAC encoder/decoder <http://flac.sourceforge.net/>`_
* `lame - Command-line MP3 encoder <http://lame.sourceforge.net/>`_
* `libdiscid - a C library for creating MusicBrainz and freedb DiscIDs from audio CDs <http://musicbrainz.org/doc/libdiscid>`_

.. note::

   The ``flac`` and ``lame`` executables must be on your ``$PATH``. The
   location of the ``libdiscid`` shared library must be specified in the
   *flacmanager.ini* configuration file (e.g. */opt/local/lib/libdiscid.dylib*).

Additionally, FLAC Manager calls the following programs which are available in
Mac OS X and should not require any special/additional configuration:

* disktool - Disk Arbitration Command Tool
* `open - open files and directories <http://developer.apple.com/library/mac/#documentation/Darwin/Reference/ManPages/10.6/man1/open.1.html>`_
* `mkdir - make directories <http://developer.apple.com/library/mac/#documentation/Darwin/Reference/ManPages/10.6/man1/mkdir.1.html>`_

