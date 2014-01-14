==================================================================
FLAC Manager --- an audio metadata aggregator and FLAC+MP3 encoder
==================================================================

:Release: |release|

FLAC Manager is a plain :py:mod:`tkinter` GUI (i.e. no :py:mod:`tkinter.tix` or
:py:mod:`tkinter.ttk`) that aggregates metadata for a CD-DA disc from the
`Gracenote Web API <https://developer.gracenote.com/web-api>`_ and the
`MusicBrainz XML Web Service <http://musicbrainz.org/doc/Development/XML_Web_Service/Version_2>`_,
allows the user to choose metadata values from the aggregated data (or enter
freeform values), and then rips tracks to FLAC and MP3. The encoding options
for FLAC and MP3 are fully configurable, and the directory/file naming scheme
is customizable.

.. warning::

   FLAC Manager was not originally intended for release; it was written as a
   personal utility to address an immediate need - to rip and tag my entire
   music collection to FLAC (for archiving) and MP3 (for import into
   `iTunes <http://www.apple.com/itunes/>`_ and upload to
   `Google Play <https://play.google.com/>`_).

   As such, FLAC Manager is developed specifically to run on my personal Mac,
   and to contain only the features/functions I need to achieve my goal. I
   have no plans to release bugfixes or new features unless they directly
   impact my ability to achieve my goal. Once my library has been ripped in
   its entirety, it is unlikely that I will maintain FLAC Manager.

   In light of this, FLAC Manager has some limitations:

   1. FLAC Manager is a Mac OS X-**only** application. It will not run on
      Windows or Linux, and *may* not run on versions of Mac OS X earlier or
      later than 10.6.8 (my current version). I do not, nor do I plan to,
      test on other OSes or other versions of Mac OS X.
   2. The UI is not "polished." There are some quirks, and the UI is anything
      but attractive. Function outweighs form for FLAC Manager, and that isn't
      going to change; as long as the application allows me to rip and tag
      my CDs, I'm not concerned at all with aesthetics, and only marginally
      concerned with usability.
   3. FLAC Manager requires Python **3.3+** to run. It is not backward-
      compatible, and no attempt will be made to make it so.

   Despite these limitations, the source code includes working examples of
   using :py:mod:`ctypes`, :py:mod:`queue`, :py:mod:`threading`,
   :py:mod:`tkinter`, and other standard Python modules under
   `Python 3.3 <http://www.python.org/download/releases/3.3.0/>`_ , so I have
   chosen to make the source code available for reference.

Table of Contents
-----------------

.. toctree::
   :maxdepth: 2

   prerequisites
   usage
   api-ref

Download and Install
--------------------

FLAC Manager can be downloaded or cloned from
`<https://bitbucket.org/mzipay/sandbox/src/tip/python/flacmanager>`_. There is
no installation process; simply drop *flacmanager.py* into a location of your
choosing and run it.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

