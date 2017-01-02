=====================================
Prerequisites for running FLACManager
=====================================

:Release: |release|

FLACManager requires `Python 3.3+
<https://www.python.org/downloads/mac-osx/>`_ and the :py:mod:`tkinter`
module (including :py:mod:`tkinter.ttk`) to run.

.. warning::
   `Apple no longer includes X11 with OS X
   <https://support.apple.com/en-us/HT201341>`_. If you encounter an
   error message indicating that the ``DISPLAY`` environment variable is
   undefined when attempting to run FLACManager, you will need to
   install `XQuartz <http://xquartz.macosforge.org/>`_.

Additionally, you **must** register for a
`Gracenote Developer <https://developer.gracenote.com/>`_ account to
obtain a user ID and client ID (which you must then provide in the
*flacmanager.ini* configuration file).

FLACManager also has several software prerequisites. Each of the
following can be installed via `MacPorts <http://www.macports.org/>`_,
or follow the links to the original project pages to download sources
and build/install yourself.

* `flac - Command-line FLAC encoder/decoder <http://flac.sourceforge.net/>`_
* `lame - Command-line MP3 encoder <http://lame.sourceforge.net/>`_
* `libdiscid - a C library for creating MusicBrainz and freedb DiscIDs from audio CDs <http://musicbrainz.org/doc/libdiscid>`_

.. note::
   The ``flac`` and ``lame`` executables must be on your ``$PATH``. The
   location of the ``libdiscid`` shared library must be specified in the
   *flacmanager.ini* configuration file (e.g.
   */opt/local/lib/libdiscid.dylib*).

Additionally, FLACManager calls the following programs which are
available in Mac OS X and should not require any special/additional
configuration:

* `diskutil - manage local disks and volumes <https://developer.apple.com/library/mac/documentation/Darwin/Reference/Manpages/man8/diskutil.8.html>`_
* `open - open files and directories <https://developer.apple.com/library/mac/documentation/Darwin/Reference/Manpages/man1/open.1.html>`_
* `mkdir - make directories <https://developer.apple.com/library/mac/documentation/Darwin/Reference/Manpages/man1/mkdir.1.html>`_

