=================================================================
FLACManager --- an audio metadata aggregator and FLAC+MP3 encoder
=================================================================

:Release: |release|

FLACManager is a plain :py:mod:`tkinter` GUI application that
aggregates metadata for a CD-DA disc from the `Gracenote Web API
<https://developer.gracenote.com/web-api>`_ and the
`MusicBrainz XML Web Service
<http://musicbrainz.org/doc/Development/XML_Web_Service/Version_2>`_,
allows the user to choose metadata values from the aggregated data (or
enter freeform values), and then rips tracks to FLAC and MP3.

All aspects of tagging and encoding are fully configurable via the
*flacmanager.ini* configuration file:

* Vorbis comments and ID3v2 tags can be mapped to specific metadata
  fields (by default, the mapping is compatible with iTunes and
  Google Play)
* encoding options for FLAC (flac) and MP3 (lame)
* file system directory and file name templates for encoded files
  (including the option to generate safe, cross-platform names)

.. versionadded:: 0.8.0
   Custom Vorbis comments and/or ID3v2 tags can be specified on a
   per-album and per-track basis.

.. versionadded:: 0.8.0
   FLACManager now auto-detects clipping in MP3s and will re-encode
   with scaled PCM data until no clipping occurs.

.. warning::
   FLACManager was not originally intended for release; it was written
   as a personal utility to address an immediate need - to rip and tag
   my entire music collection to FLAC (for archiving) and MP3 (for
   import into `iTunes <http://www.apple.com/itunes/>`_ and upload to
   `Google Play Music <https://play.google.com/store/music>`_).

   As such, FLACManager is developed specifically to run on my personal
   Mac, and to contain only the features/functions I need to achieve my
   goal. I have no plans to release bugfixes or new features unless they
   directly impact my ability to achieve my goal. Once my library has
   been ripped in its entirety, it is unlikely that I will maintain
   FLACManager.

   In light of this, FLACManager has some limitations:

   1. FLACManager is a Mac OS X-**only** application. It will not run on
      Windows or Linux, and *may* not run on versions of Mac OS X
      earlier than 10.6.8.
   2. The UI is not "polished." There are some quirks, and the UI is
      anything but attractive. Function outweighs form for FLACManager,
      and that isn't going to change; as long as the application allows
      me to rip and tag my CDs, I'm not concerned at all with
      aesthetics, and only marginally concerned with usability.
   3. FLACManager requires Python **3.3+** to run. It is not backward-
      compatible, and no attempt will be made to make it so.

   Despite these limitations, the source code includes working examples
   of using :py:mod:`ctypes`, :py:mod:`queue`, :py:mod:`threading`,
   :py:mod:`tkinter`, and other standard Python modules under
   `Python 3.3+ <http://docs.python.org/3>`_ , so I have chosen to make
   the source code available for reference.

Table of Contents
-----------------

.. toctree::
   :maxdepth: 2

   prerequisites
   whats-new
   usage
   api-ref

Download and Install
--------------------

Clone or fork the repository from GitHub::

   $ git clone https://github.com/mzipay/FLACManager.git

Alternatively, download a *.tar.gz* or *.zip* source archive from
https://github.com/mzipay/FLACManager/releases.

There is no installation process; simply run ``python flacmanager.py``.
This will create the default *flacmanager.ini* configuration file and
prompt you to provide several required values.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

